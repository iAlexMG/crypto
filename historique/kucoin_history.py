#!/usr/bin/env python3
r"""Extracteur d'historique de trades KuCoin Futures — AUTONOME (stdlib seule).

Quatrieme venue du montage (pendant de `binance_history.py` / `bybit_history.py` /
`okx_history.py` : un seul fichier, stdlib pure, zero import du projet, copiable tel
quel). Normalise au schema commun du projet (ts ms UTC, prix, size en ACTIF DE BASE,
side = cote agresseur).

POURQUOI archives seules (pas de queue REST) ?
  KuCoin publie un portail officiel d'archives S3 (historical-data.kucoin.com) :
  UN zip par jour et par contrat sous
  data/futures/daily/trades/<SYM>/<SYM>-trades-<AAAA-MM-JJ>.zip (+ .zip.CHECKSUM),
  couverture depuis 2023-01-01 pour BTCUSDTM, publication ~1 jour de retard, et le
  fichier est BORNE UTC [00:00, 24:00) — pas le piege UTC+8 d'OKX (verifie sur le
  temoin 2026-07-10 : 00:00:00.656 -> 23:59:25.645 UTC). Le REST public futures
  (/api/v1/trade/history) rend les 100 DERNIERS trades sans aucune pagination
  (verifie) — meme constat que Bybit : inutilisable pour l'historique, et inutile
  ici puisque l'archive couvre les jours pleins.

PARTICULARITES KUCOIN (mesurees sur fichier temoin 2026-07-10) :
  - TROIS noms pour le meme contrat : symbole commun `BTCUSDT` (nommage du projet),
    dossier/fichiers d'archives `BTCUSDTM` (M = perp margine USDT), contrat API
    `XBTUSDTM` (XBT = ancien code BTC, seule paire renommee : les autres sont
    <BASE>USDTM partout). Le dossier d'archives XBTUSDTM existe mais est VIDE ;
  - archive = ZIP -> 1 CSV avec entete `trade_id,trade_time,price,size,side` ;
  - >> PIEGE `trade_id` : entiers UNIQUES mais PAS une cle d'ordre — DEUX regimes
     d'ids coexistent (~1,9e12 et ~1,0e17 ; 47 ids « hauts » sur 116 438 le temoin)
     et 35 944 inversions id<->ts dans un fichier pourtant chronologique -> on
     N'UTILISE PAS cet id : `trade_id` = rowid d'insertion, comme Bybit/UUID.
     Consequences identiques a bybit_history.py :
       * dedup impossible par id -> chaque fichier est ingere dans UNE transaction
         (donnees + marqueur `_ingested`) : interrompu = rollback, jamais de doublon ;
       * ordre chronologique des rowids GARANTI : flux verifie monotone a
         l'ingestion (repli : tri stable du fichier), backfill AVANT le contenu
         existant refuse (hypothese de candles.py : rowid = chrono) ;
  - `trade_time` en MILLISECONDES epoch (le REST, lui, sert des NANOsecondes —
    piege documente mais non utilise ici) ;
  - >> PIEGE `side` : QUATRE casses dans le meme fichier (buy/BUY/sell/SELL,
     temoin : 22 726 / 33 933 / 24 971 / 34 808) -> normalisation .lower() ;
  - >> PIEGE `size` en NOMBRE DE CONTRATS (entiers), multiplier = 0,001 BTC pour
     XBTUSDTM (meme piege qu'OKX/ctVal) : on MULTIPLIE pour stocker des BTC.
     Multiplier lu sur /api/v1/contracts/<contrat API> (repli : table MULTIPLIER).
     Verifie : temoin 2026-07-10 = 116 438 trades, 3 574 BTC — venue ~17x plus
     petite que Bybit (59 391 BTC) et ~22x qu'OKX (79 642 BTC) le meme jour ;
  - .zip.CHECKSUM = MD5 (32 hex, format `<md5>  <nom>.zip`) — pas SHA-256 comme
    Binance ; verifie en plus du CRC des membres du zip ;
  - le plan vient du LISTING S3 reel (XML ListObjects pagine par marker), pas de
    404 devine — meme philosophie que Bybit.

SORTIE : SQLite (defaut) `data\<SYMBOLE>-kucoin-perp-api.db` — nommage standard
  `<SYMBOLE>-<exchange>-<marche>-<source>.db` — schema identique aux autres voies
  (`trades`, `_meta`, `_ingested`, index ts differe) ; ou CSV gzip normalise.

EXEMPLES :
  # voir le plan sans rien telecharger
  python kucoin_history.py --start 2026-06-01 --end 2026-07-11 --dry-run

  # la cible du projet : fenetre commune des pages -> data\BTCUSDT-kucoin-perp-api.db
  python kucoin_history.py --start 2026-06-01 --end 2026-07-11

  # une fenetre precise -> CSV gzip
  python kucoin_history.py --start 2026-06-01 --end 2026-06-07 --format csv --out ./csv_out
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile

# --------------------------------------------------------------------------- #
# Constantes / endpoints
# --------------------------------------------------------------------------- #
S3 = "https://historical-data.kucoin.com"
TRADES_PREFIX = "data/futures/daily/trades"
REST = "https://api-futures.kucoin.com"


def archive_symbol(symbol: str) -> str:
    """BTCUSDT -> BTCUSDTM (dossier d'archives ; M = perp margine USDT)."""
    s = symbol.upper()
    if s.endswith("USDT"):
        return s + "M"
    raise SystemExit(f"Symbole non gere : {symbol} (attendu <BASE>USDT, ex. BTCUSDT).")


def contract_symbol(symbol: str) -> str:
    """BTCUSDT -> XBTUSDTM (contrat API ; seul BTC est reste XBT cote API)."""
    s = archive_symbol(symbol)
    return "XBT" + s[3:] if s.startswith("BTC") else s


# multiplier de repli (actif de base par contrat) si l'API contracts est injoignable.
MULTIPLIER = {"XBTUSDTM": 0.001, "ETHUSDTM": 0.01}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UA = {"User-Agent": "Mozilla/5.0 (kucoin-history-extractor)"}

BATCH = 50_000  # lignes par executemany


# --------------------------------------------------------------------------- #
# HTTP (avec retries)
# --------------------------------------------------------------------------- #
def http_get(url: str, retries: int = 5, timeout: int = 60) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 - reseau, on reessaie
            last = exc
            time.sleep(min(2 ** i, 30))
    raise last  # type: ignore[misc]


def download_file(url: str, dest: str, retries: int = 5, timeout: int = 180) -> None:
    """Telecharge en streaming vers `dest` (.part puis rename, pour atomicite)."""
    tmp = dest + ".part"
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            time.sleep(min(2 ** i, 30))
    raise last  # type: ignore[misc]


def verify_zip(path: str, md5_expected: str | None) -> bool:
    """MD5 du .zip.CHECKSUM (quand disponible) PUIS CRC de chaque membre du zip.
    Faux si tronque/corrompu/somme differente."""
    if md5_expected:
        h = hashlib.md5()
        try:
            with open(path, "rb") as f:
                while chunk := f.read(1 << 20):
                    h.update(chunk)
        except OSError:
            return False
        if h.hexdigest() != md5_expected:
            return False
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except (zipfile.BadZipFile, OSError, EOFError):
        return False


def fetch_checksum(url_zip: str) -> str | None:
    """MD5 attendu depuis <url>.CHECKSUM (`<md5>  <nom>.zip`) ; None si indisponible."""
    try:
        txt = http_get(url_zip + ".CHECKSUM", retries=2).decode("ascii", "replace")
        m = re.match(r"^([0-9a-fA-F]{32})\b", txt.strip())
        return m.group(1).lower() if m else None
    except Exception:  # noqa: BLE001 - la somme est un plus, le CRC zip reste
        return None


def fetch_multiplier(contract: str) -> float:
    """multiplier (actif de base par contrat) via l'API publique ; repli sur la table."""
    try:
        d = json.loads(http_get(f"{REST}/api/v1/contracts/{contract}").decode())
        if d.get("code") == "200000" and d.get("data"):
            return float(d["data"]["multiplier"])
    except Exception:  # noqa: BLE001 - repli silencieux
        pass
    if contract in MULTIPLIER:
        return MULTIPLIER[contract]
    raise SystemExit(f"multiplier introuvable pour {contract} (API muette, pas de repli).")


# --------------------------------------------------------------------------- #
# Plan de telechargement — DEPUIS le listing S3 reel (XML pagine par marker)
# --------------------------------------------------------------------------- #
class Item:
    __slots__ = ("name", "url", "period")

    def __init__(self, name: str, url: str, period: str) -> None:
        self.name = name      # ex. "daily/2026-06-01" (meme convention que les autres)
        self.url = url
        self.period = period  # "2026-06-01"


def parse_day(s: str | None, last: bool) -> dt.date | None:
    """'YYYY' | 'YYYY-MM' | 'YYYY-MM-DD' -> date de debut (ou de FIN si last=True)."""
    if not s:
        return None
    parts = [int(x) for x in s.split("-")]
    y = parts[0]
    m = parts[1] if len(parts) > 1 else (12 if last else 1)
    if len(parts) > 2:
        return dt.date(y, m, parts[2])
    if not last:
        return dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def list_bucket(prefix: str, marker: str | None = None):
    """Iterateur des <Key> du bucket sous `prefix` (ListObjects V1, 1000/page)."""
    while True:
        q = {"prefix": prefix}
        if marker:
            q["marker"] = marker
        xml = http_get(f"{S3}/?{urllib.parse.urlencode(q)}").decode("utf-8", "replace")
        keys = re.findall(r"<Key>([^<]+)</Key>", xml)
        yield from keys
        if "<IsTruncated>true</IsTruncated>" not in xml or not keys:
            return
        marker = keys[-1]  # V1 sans delimiter : le marker suivant = derniere cle


def build_plan(arch_sym: str, start: str | None, end: str | None) -> list[Item]:
    """Liste les zips reellement publies pour `arch_sym` et filtre sur la fenetre.
    Le marker initial saute directement a la veille de --start (le dossier remonte
    a 2023 : ~2 600 cles sinon)."""
    prefix = f"{TRADES_PREFIX}/{arch_sym}/"
    d0, d1 = parse_day(start, last=False), parse_day(end, last=True)
    marker = None
    if d0 is not None:
        prev = d0 - dt.timedelta(days=1)
        marker = f"{prefix}{arch_sym}-trades-{prev.isoformat()}.zip.CHECKSUM"
    pat = re.compile(rf"{re.escape(prefix)}{re.escape(arch_sym)}-trades-"
                     rf"(\d{{4}}-\d{{2}}-\d{{2}})\.zip$")
    plan = []
    for key in list_bucket(prefix, marker):
        m = pat.match(key)
        if not m:
            continue
        day = dt.date.fromisoformat(m.group(1))
        if d0 is not None and day < d0:
            continue
        if d1 is not None and day > d1:
            break  # listing trie : rien d'utile au-dela de la fenetre
        plan.append(Item(f"daily/{m.group(1)}", f"{S3}/{key}", m.group(1)))
    if not plan and d0 is None:
        raise SystemExit(f"Aucune archive trouvee pour {arch_sym} sous {S3}/{prefix}. "
                         f"Verifie le symbole (perp USDT seulement, ex. BTCUSDT).")
    return plan


# --------------------------------------------------------------------------- #
# Parsing d'un zip KuCoin -> (ts_ms, price, size_base, side)
# Entete : trade_id,trade_time,price,size,side. L'id est IGNORE (deux regimes,
# pas une cle d'ordre — voir docstring). size (contrats) * multiplier = actif de
# base. side normalise .lower() (4 casses observees). Entete saute (champ 1 non
# numerique).
# --------------------------------------------------------------------------- #
def iter_rows(zip_path: str, mult: float):
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        with z.open(name) as raw:
            for r in csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline="")):
                if not r:
                    continue
                try:
                    ts = int(r[1])
                except (ValueError, IndexError):
                    continue  # ligne d'entete
                yield (ts, float(r[2]), float(r[3]) * mult,
                       "buy" if r[4].lower() == "buy" else "sell")


# --------------------------------------------------------------------------- #
# Ecrivains : SQLite et CSV gzip
# --------------------------------------------------------------------------- #
class SqliteSink:
    def __init__(self, path: str, symbol: str, mult: float) -> None:
        first = not os.path.exists(path)
        self.conn = sqlite3.connect(path)
        c = self.conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-262144")
        c.execute("""CREATE TABLE IF NOT EXISTS trades(
                        trade_id INTEGER PRIMARY KEY,   -- = rowid : append chronologique
                        ts       INTEGER NOT NULL,
                        price    REAL    NOT NULL,
                        size     REAL    NOT NULL,
                        side     TEXT    NOT NULL)""")
        # INDEX ts DIFFERE comme les autres voies : construit en une fois a la fin
        # du plan complet (ensure_ts_index) -> l'ingestion reste un simple append.
        c.execute("""CREATE TABLE IF NOT EXISTS _ingested(
                        name TEXT PRIMARY KEY, rows INTEGER, at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS _meta(k TEXT PRIMARY KEY, v TEXT)""")
        if first:
            for k, v in (("symbol", symbol), ("market", "swap"), ("exchange", "kucoin"),
                         ("contract_val", str(mult)), ("source", "kucoin-public-archives")):
                c.execute("INSERT OR REPLACE INTO _meta VALUES(?,?)", (k, v))
        c.commit()

    def done(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM _ingested")}

    def max_ts(self) -> int | None:
        r = self.conn.execute("SELECT max(ts) FROM trades").fetchone()
        return r[0] if r and r[0] is not None else None

    def ingest(self, item: Item, zip_path: str, mult: float) -> tuple[int, int]:
        """UNE transaction par fichier (donnees + marqueur `_ingested`) : sans id de
        dedup, l'atomicite remplace l'INSERT OR IGNORE. Le flux est insere tel quel
        s'il est chronologique (cas normal mesure) ; a la premiere inversion,
        rollback et seconde passe TRIEE (tri stable : a ts egal, ordre du fichier)."""
        c = self.conn
        try:
            rows = self._insert_streaming(item, zip_path, mult)
        except _OutOfOrder:
            c.rollback()
            print(f"    (flux non chronologique -> tri de {item.period})")
            rows = self._insert_sorted(item, zip_path, mult)
        c.commit()  # data + marqueur de reprise = meme transaction
        return rows, rows

    def _insert_streaming(self, item: Item, zip_path: str, mult: float) -> int:
        c = self.conn
        cur = c.cursor()
        cur.execute("BEGIN")
        rows = 0
        prev = -1
        batch: list[tuple] = []
        for ts, price, size, side in iter_rows(zip_path, mult):
            if ts < prev:
                raise _OutOfOrder
            prev = ts
            batch.append((ts, price, size, side))
            if len(batch) >= BATCH:
                cur.executemany(
                    "INSERT INTO trades(ts,price,size,side) VALUES(?,?,?,?)", batch)
                rows += len(batch)
                batch.clear()
        if batch:
            cur.executemany("INSERT INTO trades(ts,price,size,side) VALUES(?,?,?,?)", batch)
            rows += len(batch)
        self._mark(cur, item, rows)
        return rows

    def _insert_sorted(self, item: Item, zip_path: str, mult: float) -> int:
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        # tri STABLE sur ts seul : a ts egal, l'ordre du fichier (= ordre exchange) prime
        data = sorted(iter_rows(zip_path, mult), key=lambda r: r[0])
        cur.executemany("INSERT INTO trades(ts,price,size,side) VALUES(?,?,?,?)", data)
        self._mark(cur, item, len(data))
        return len(data)

    @staticmethod
    def _mark(cur: sqlite3.Cursor, item: Item, rows: int) -> None:
        cur.execute("INSERT OR REPLACE INTO _ingested VALUES(?,?,?)",
                    (item.name, rows, dt.datetime.now(dt.timezone.utc).isoformat()))

    def has_ts_index(self) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_trades_ts'").fetchone()
        return r is not None

    def ensure_ts_index(self) -> None:
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class _OutOfOrder(Exception):
    """Flux du fichier non chronologique : on repasse en mode trie."""


class CsvSink:
    """Un .csv.gz normalise par jour. Reprise = presence du fichier final."""
    def __init__(self, out_dir: str, symbol: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        self.dir = out_dir
        self.symbol = symbol

    def _path(self, item: Item) -> str:
        return os.path.join(self.dir, f"{self.symbol}-kucoin-trades-{item.period}.csv.gz")

    def done(self) -> set[str]:
        names: set[str] = set()
        for fn in os.listdir(self.dir) if os.path.isdir(self.dir) else []:
            if fn.endswith(".csv.gz"):
                tag = fn.removeprefix(f"{self.symbol}-kucoin-trades-").removesuffix(".csv.gz")
                names.add(f"daily/{tag}")
        return names

    def max_ts(self) -> int | None:
        return None  # fichiers independants : pas de contrainte d'ordre global

    def ingest(self, item: Item, zip_path: str, mult: float) -> tuple[int, int]:
        final = self._path(item)
        tmp = final + ".part"
        rows = 0
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
            w = csv.writer(gz)
            w.writerow(["ts", "price", "size", "side"])
            for ts, price, size, side in sorted(iter_rows(zip_path, mult), key=lambda r: r[0]):
                w.writerow([ts, price, size, side])
                rows += 1
        os.replace(tmp, final)
        return rows, rows

    def has_ts_index(self) -> bool:
        return True

    def ensure_ts_index(self) -> None:
        pass

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Producteur (telechargement + MD5/CRC) -> file bornee -> consommateur (ingest)
# --------------------------------------------------------------------------- #
def producer(plan: list[Item], cache: str, q: "queue.Queue", stop: threading.Event) -> None:
    for it in plan:
        if stop.is_set():
            break
        dest = os.path.join(cache, it.url.rsplit("/", 1)[-1])
        try:
            md5 = fetch_checksum(it.url)
            if not (os.path.exists(dest) and verify_zip(dest, md5)):
                download_file(it.url, dest)
                if not verify_zip(dest, md5):
                    download_file(it.url, dest)  # un retelechargement avant d'abandonner
                    if not verify_zip(dest, md5):
                        raise RuntimeError("zip corrompu (MD5/CRC)")
            q.put((it, dest))
        except Exception as exc:  # noqa: BLE001
            q.put(("ERROR", f"{it.name}: {exc}"))
            return
    q.put((None, None))  # sentinelle de fin


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def run(args: argparse.Namespace) -> int:
    symbol = args.symbol.upper()
    arch = archive_symbol(symbol)
    contract = contract_symbol(symbol)
    print(f"== KuCoin history — {symbol} (archives {arch}, contrat API {contract}) ==")
    mult = fetch_multiplier(contract)
    print(f"multiplier = {mult} (size en contrats x multiplier = actif de base)")

    print("Construction du plan (listing S3 historical-data.kucoin.com)…")
    plan = build_plan(arch, args.start, args.end)
    if not plan:
        print("Plan vide (aucune archive publiee dans la fenetre).")
        return 0

    if args.format == "sqlite":
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-kucoin-perp-api.db")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        sink: SqliteSink | CsvSink = SqliteSink(out, symbol, mult)
    else:
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-kucoin-perp-trades")
        sink = CsvSink(out, symbol)

    done = sink.done()
    todo = [it for it in plan if it.name not in done]

    # Garde-fou chronologie (hypothese candles.py : rowid = ordre chrono) : sans id de
    # trade utilisable, un backfill AVANT le contenu existant casserait l'ordre des rowids.
    if todo and (mx := sink.max_ts()) is not None:
        max_day = dt.datetime.fromtimestamp(mx / 1000, dt.timezone.utc).date()
        first_new = dt.date.fromisoformat(todo[0].period)
        if first_new <= max_day:
            print(f"REFUS : la base contient deja des trades jusqu'au {max_day} ; "
                  f"ingerer {first_new} casserait l'ordre chronologique des rowids.\n"
                  f"-> pour du backfill, reconstruire la base sur la fenetre complete "
                  f"(ou utiliser --out vers une nouvelle base).")
            sink.close()
            return 1

    span = f"{plan[0].period} … {plan[-1].period}"
    print(f"Plan : {len(plan)} fichiers ({span}) ; deja fait : "
          f"{len(done & {p.name for p in plan})} ; a traiter : {len(todo)}")
    print(f"Sortie : {out}  (format={args.format})")

    if args.dry_run:
        for it in todo[:10]:
            print("  -", it.name, "->", it.url)
        if len(todo) > 10:
            print(f"  … (+{len(todo) - 10} autres)")
        print("DRY-RUN : rien telecharge.")
        sink.close()
        return 0

    def finalize_if_complete() -> None:
        if [it for it in plan if it.name not in sink.done()]:
            print("Plan non termine -> l'index ts sera construit au run qui le complete.")
            return
        if not sink.has_ts_index():
            print("Plan complet -> construction de l'index ts (differe)…")
            t = time.monotonic()
            sink.ensure_ts_index()
            print(f"  index ts OK ({time.monotonic() - t:.0f}s)")

    if not todo:
        print("Tout est deja a jour.")
        finalize_if_complete()
        sink.close()
        return 0

    os.makedirs(args.cache, exist_ok=True)
    q: "queue.Queue" = queue.Queue(maxsize=max(1, args.prefetch))
    stop = threading.Event()
    th = threading.Thread(target=producer, args=(todo, args.cache, q, stop),
                          name="downloader", daemon=True)
    th.start()

    t0 = time.monotonic()
    total_rows = total_new = 0
    i = 0
    try:
        while True:
            it, dest = q.get()
            if it is None:                       # fin normale
                break
            if it == "ERROR":                    # echec producteur -> stop (reprenable)
                print(f"\n!! Echec telechargement {dest}. Relance la commande pour reprendre.")
                break
            i += 1
            r, n = sink.ingest(it, dest, mult)
            total_rows += r
            total_new += n
            if not args.keep_zip:
                try:
                    os.remove(dest)
                except OSError:
                    pass
            el = time.monotonic() - t0
            rate = int(total_rows / el) if el > 0 else 0
            print(f"[{i:>4}/{len(todo)}] {it.name:<18} "
                  f"{human(r):>12} lignes | cumul {human(total_rows)} | "
                  f"{human(rate)}/s | {el:6.0f}s", flush=True)
    except KeyboardInterrupt:
        print("\nInterrompu — etat sauvegarde (reprenable en relancant).")
        stop.set()
    finally:
        stop.set()

    finalize_if_complete()

    if isinstance(sink, SqliteSink):
        lo = sink.conn.execute("SELECT min(ts), max(ts), count(*) FROM trades").fetchone()
        if lo and lo[2]:
            def iso(ms): return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()
            print(f"Base : {human(lo[2])} trades, {iso(lo[0])} → {iso(lo[1])}")
    sink.close()
    print(f"Termine : +{human(total_new)} trades en {time.monotonic() - t0:.0f}s.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extracteur autonome d'historique de trades KuCoin Futures "
                    "(portail public historical-data.kucoin.com).")
    p.add_argument("--symbol", default="BTCUSDT",
                   help="symbole commun, defaut BTCUSDT (-> archives BTCUSDTM, contrat XBTUSDTM)")
    p.add_argument("--start", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (defaut: debut dispo)")
    p.add_argument("--end", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (defaut: dernier publie)")
    p.add_argument("--format", default="sqlite", choices=["sqlite", "csv"])
    p.add_argument("--out", default=None,
                   help="fichier .db (sqlite) ou dossier (csv) ; defaut : data\\<SYMBOLE>-kucoin-perp-api.db")
    p.add_argument("--cache", default=os.path.join(DATA_DIR, "_dumps"),
                   help="cache des zip telecharges (defaut : data\\_dumps)")
    p.add_argument("--prefetch", type=int, default=2, help="fichiers telecharges en avance")
    p.add_argument("--keep-zip", action="store_true", help="garder les zip bruts apres ingestion")
    p.add_argument("--dry-run", action="store_true", help="afficher le plan sans telecharger")
    args = p.parse_args()
    try:
        return run(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
