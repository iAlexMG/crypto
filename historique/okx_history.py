#!/usr/bin/env python3
r"""Extracteur d'historique de trades OKX — AUTONOME (stdlib seule).

Troisieme venue du montage (pendant de `binance_history.py` / `bybit_history.py` :
un seul fichier, stdlib pure, zero import du projet, copiable tel quel). Normalise
au schema commun du projet (ts ms UTC, prix, size en ACTIF DE BASE, side = cote
agresseur).

POURQUOI archives + REST ?
  OKX publie UN fichier zip par jour et par instrument sur son CDN public
  (www.okx.com/cdn/okex/traderecords/trades/daily/...), en libre acces, remontant a
  fin 2021 pour BTC-USDT-SWAP. Mais l'archive est BORNEE EN HEURE DE HONG KONG
  (UTC+8) et publiee avec ~1 jour de retard : le fichier <AAAA-MM-JJ> couvre en fait
  [JJ-1 16:00 UTC, JJ 16:00 UTC). Pour completer la queue recente jusqu'a une borne
  UTC precise, on pagine le REST `history-trades` (--rest-until) : meme donnee, memes
  ids entiers. (Le REST seul serait inutilisable pour tout l'historique : 100/page.)

PARTICULARITES OKX (mesurees sur fichier temoin 2026-07-10) :
  - archive = ZIP -> 1 CSV avec entete
    `instrument_name,trade_id,side,price,size,created_time` ;
  - `trade_id` = ENTIER strictement contigu (+1) -> vraie cle de dedup et ordre
    chronologique garanti (comme Binance, PAS comme Bybit/UUID) : PK INTEGER, rowid ;
  - `side` (buy/sell) = cote AGRESSEUR direct (pas d'inversion) ;
  - `created_time` en MILLISECONDES epoch ;
  - >> PIEGE `size` en NOMBRE DE CONTRATS, pas en actif de base : BTC-USDT-SWAP a
     ctVal = 0,01 BTC -> on MULTIPLIE par ctVal pour stocker des BTC (verifie : la
     journee 2026-07-10 fait 86 201 BTC avec x0,01, absurde sans). ctVal lu sur
     /api/v5/public/instruments (repli : table CONTRACT_VAL) et conserve dans _meta ;
  - >> PIEGE symbole : instId OKX = `BTC-USDT-SWAP` (perp) ; on RE-NORMALISE vers le
     symbole commun `BTCUSDT` pour le nom de base et _meta (coherent multi-exchange) ;
  - >> PIEGE HTTP 403 : OKX bloque l'User-Agent par defaut d'urllib -> UA navigateur.

SORTIE : SQLite (defaut) `data\<SYMBOLE>-okx-perp-api.db` — nommage standard
  `<SYMBOLE>-<exchange>-<marche>-<source>.db` — schema identique aux autres voies
  (`trades`, `_meta`, `_ingested`, index ts differe) ; ou CSV gzip normalise.

EXEMPLES :
  # voir le plan sans rien telecharger
  python okx_history.py --start 2026-06-01 --end 2026-07-12 --dry-run

  # la cible du projet : archives de la fenetre, PUIS queue REST jusqu'a 2026-07-12 UTC
  python okx_history.py --start 2026-06-01 --end 2026-07-12 --rest-until 2026-07-12

  # une fenetre precise -> CSV gzip
  python okx_history.py --start 2026-06-01 --end 2026-06-07 --format csv --out ./csv_out
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import os
import queue
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
CDN = "https://www.okx.com/cdn/okex/traderecords/trades/daily"
REST = "https://www.okx.com"

# instId OKX pour un symbole commun (perp = SWAP lineaire USDT).
def inst_id(symbol: str) -> str:
    """BTCUSDT -> BTC-USDT-SWAP (perp). Suppose une paire ...USDT."""
    s = symbol.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}-USDT-SWAP"
    raise SystemExit(f"Symbole non gere : {symbol} (attendu <BASE>USDT, ex. BTCUSDT).")

# ctVal de repli (BTC par contrat) si l'API instruments est injoignable.
CONTRACT_VAL = {"BTC-USDT-SWAP": 0.01, "ETH-USDT-SWAP": 0.1}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

BATCH = 50_000  # lignes par executemany


# --------------------------------------------------------------------------- #
# HTTP (avec retries) — 404 remonte tel quel (fichier non publie / hors plage)
# --------------------------------------------------------------------------- #
class NotFound(Exception):
    """404 : archive du jour non publiee (retard CDN) ou hors couverture."""


def http_get(url: str, retries: int = 5, timeout: int = 60) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(url) from exc
            last = exc
            time.sleep(min(2 ** i, 30))
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
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise NotFound(url) from exc
            last = exc
            time.sleep(min(2 ** i, 30))
        except Exception as exc:  # noqa: BLE001
            last = exc
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            time.sleep(min(2 ** i, 30))
    raise last  # type: ignore[misc]


def verify_zip(path: str) -> bool:
    """Le CRC de chaque membre du zip fait foi (OKX ne publie pas de somme separee).
    Faux si tronque/corrompu."""
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except (zipfile.BadZipFile, OSError, EOFError):
        return False


def fetch_ctval(instid: str) -> float:
    """ctVal (actif de base par contrat) via l'API publique ; repli sur la table."""
    try:
        url = f"{REST}/api/v5/public/instruments?instType=SWAP&instId={instid}"
        d = json.loads(http_get(url).decode())
        if d.get("code") == "0" and d.get("data"):
            return float(d["data"][0]["ctVal"])
    except Exception:  # noqa: BLE001 - repli silencieux
        pass
    if instid in CONTRACT_VAL:
        return CONTRACT_VAL[instid]
    raise SystemExit(f"ctVal introuvable pour {instid} (API muette, pas de repli).")


# --------------------------------------------------------------------------- #
# Plan de telechargement — dates JOURNALIERES generees, existence a la reception
# (le CDN OKX n'a pas de listing) : un 404 en fin de plan = jour pas encore publie.
# --------------------------------------------------------------------------- #
class Item:
    __slots__ = ("name", "url", "period")

    def __init__(self, name: str, url: str, period: str) -> None:
        self.name = name      # ex. "daily/2026-06-01"
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


def build_plan(instid: str, start: str | None, end: str | None) -> list[Item]:
    """Genere un Item par jour de [start, end]. NB : le fichier <AAAA-MM-JJ> couvre
    [JJ-1 16:00, JJ 16:00) UTC (bornage UTC+8) — c'est le script de comparaison qui
    filtre ensuite sur la fenetre UTC voulue."""
    d0 = parse_day(start, last=False) or dt.date(2021, 1, 1)
    d1 = parse_day(end, last=True) or dt.datetime.now(dt.timezone.utc).date()
    plan = []
    d = d0
    while d <= d1:
        iso = d.isoformat()
        url = f"{CDN}/{d.strftime('%Y%m%d')}/{instid}-trades-{iso}.zip"
        plan.append(Item(f"daily/{iso}", url, iso))
        d += dt.timedelta(days=1)
    return plan


# --------------------------------------------------------------------------- #
# Parsing d'un zip OKX -> (trade_id, ts_ms, price, size_base, side)
# Entete : instrument_name,trade_id,side,price,size,created_time
# size (contrats) * ctVal = actif de base. Entete saute (trade_id non numerique).
# --------------------------------------------------------------------------- #
def iter_rows(zip_path: str, ctval: float):
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        with z.open(name) as raw:
            for r in csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline="")):
                if not r:
                    continue
                try:
                    tid = int(r[1])
                except (ValueError, IndexError):
                    continue  # ligne d'entete
                yield (tid, int(r[5]), float(r[3]), float(r[4]) * ctval,
                       "buy" if r[2] == "buy" else "sell")


# --------------------------------------------------------------------------- #
# Ecrivains : SQLite et CSV gzip
# --------------------------------------------------------------------------- #
class SqliteSink:
    def __init__(self, path: str, symbol: str, ctval: float) -> None:
        first = not os.path.exists(path)
        self.conn = sqlite3.connect(path)
        c = self.conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-1048576")  # ~1 Go de cache page
        c.execute("""CREATE TABLE IF NOT EXISTS trades(
                        trade_id INTEGER PRIMARY KEY,   -- entier OKX contigu : dedup + ordre chrono
                        ts       INTEGER NOT NULL,
                        price    REAL    NOT NULL,
                        size     REAL    NOT NULL,
                        side     TEXT    NOT NULL)""")
        # INDEX ts DIFFERE : construit en une fois a la fin du plan complet
        # (ensure_ts_index) -> l'ingestion reste un simple upsert par id.
        c.execute("""CREATE TABLE IF NOT EXISTS _ingested(
                        name TEXT PRIMARY KEY, rows INTEGER, at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS _meta(k TEXT PRIMARY KEY, v TEXT)""")
        if first:
            for k, v in (("symbol", symbol), ("market", "swap"), ("exchange", "okx"),
                         ("contract_val", str(ctval)), ("source", "okx-public-archives")):
                c.execute("INSERT OR REPLACE INTO _meta VALUES(?,?)", (k, v))
        c.commit()

    def done(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM _ingested")}

    def ingest(self, item: Item, zip_path: str, ctval: float) -> tuple[int, int]:
        c = self.conn
        before = c.total_changes
        rows = 0
        cur = c.cursor()
        batch: list[tuple] = []
        for row in iter_rows(zip_path, ctval):
            batch.append(row)
            if len(batch) >= BATCH:
                cur.executemany("INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?)", batch)
                rows += len(batch)
                batch.clear()
        if batch:
            cur.executemany("INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?)", batch)
            rows += len(batch)
        new = c.total_changes - before
        c.execute("INSERT OR REPLACE INTO _ingested VALUES(?,?,?)",
                  (item.name, rows, dt.datetime.now(dt.timezone.utc).isoformat()))
        c.commit()  # data + marqueur de reprise = meme transaction
        return rows, new

    def last_ts(self) -> int | None:
        r = self.conn.execute("SELECT max(ts) FROM trades").fetchone()
        return r[0] if r and r[0] is not None else None

    def max_id(self) -> int | None:
        r = self.conn.execute("SELECT max(trade_id) FROM trades").fetchone()
        return r[0] if r and r[0] is not None else None

    def insert_rows(self, rows: list[tuple]) -> int:
        before = self.conn.total_changes
        self.conn.executemany("INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?)", rows)
        self.conn.commit()
        return self.conn.total_changes - before

    def has_ts_index(self) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_trades_ts'").fetchone()
        return r is not None

    def ensure_ts_index(self) -> None:
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class CsvSink:
    """Un .csv.gz normalise par jour. Reprise = presence du fichier final."""
    def __init__(self, out_dir: str, symbol: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        self.dir = out_dir
        self.symbol = symbol

    def _path(self, item: Item) -> str:
        return os.path.join(self.dir, f"{self.symbol}-okx-trades-{item.period}.csv.gz")

    def done(self) -> set[str]:
        names: set[str] = set()
        for fn in os.listdir(self.dir) if os.path.isdir(self.dir) else []:
            if fn.endswith(".csv.gz"):
                tag = fn.removeprefix(f"{self.symbol}-okx-trades-").removesuffix(".csv.gz")
                names.add(f"daily/{tag}")
        return names

    def last_ts(self) -> int | None:
        return None

    def max_id(self) -> int | None:
        return None

    def ingest(self, item: Item, zip_path: str, ctval: float) -> tuple[int, int]:
        final = self._path(item)
        tmp = final + ".part"
        rows = 0
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
            w = csv.writer(gz)
            w.writerow(["trade_id", "ts", "price", "size", "side"])
            for tid, ts, price, size, side in iter_rows(zip_path, ctval):
                w.writerow([tid, ts, price, size, side])
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
# REST tail : complete la queue (archive bornee UTC+8) jusqu'a une borne UTC.
# history-trades, newest-first, limit max 100. DEUX modes de pagination :
#   type=2 `after`=ts ms -> SEEK : les trades juste AVANT la borne (verifie) ;
#   type=1 `after`=tradeId -> les trades plus ANCIENS que cet id.
# On se teleporte donc a `until_ms` puis on descend jusqu'a la couture d'archive —
# on ne paye que le trou reel, pas la marche arriere depuis maintenant (qui coutait
# ~12 000 pages et grossissait d'heure en heure).
# REPRISE : le plancher (id max AVANT comblement = couture) est persiste dans _meta
# le temps du comblement ; sans ca, une interruption au milieu laisserait max_id()
# au HAUT du trou et une relance s'arreterait aussitot, bas du trou jamais rempli.
# --------------------------------------------------------------------------- #
def rest_fill(sink: SqliteSink, instid: str, ctval: float, until_ms: int,
              pause: float = 0.12) -> int:
    c = sink.conn
    r = c.execute("SELECT v FROM _meta WHERE k='rest_fill_floor'").fetchone()
    if r is not None:
        floor = int(r[0])
        print(f"    (reprise d'un comblement interrompu : plancher {floor})")
    else:
        floor = sink.max_id() or 0
        c.execute("INSERT OR REPLACE INTO _meta VALUES('rest_fill_floor',?)", (str(floor),))
        c.commit()
    total = 0
    after: str | None = None  # None = seek initial par timestamp
    pages = 0
    while True:
        if after is None:
            q = {"instId": instid, "type": "2", "after": str(until_ms), "limit": "100"}
        else:
            q = {"instId": instid, "type": "1", "after": after, "limit": "100"}
        url = f"{REST}/api/v5/market/history-trades?{urllib.parse.urlencode(q)}"
        d = json.loads(http_get(url).decode())
        data = d.get("data") or []
        if not data:
            break
        pages += 1
        rows = []
        page_min_id = None
        reached = False
        for x in data:
            tid = int(x["tradeId"])
            ts = int(x["ts"])
            page_min_id = tid if page_min_id is None else min(page_min_id, tid)
            if tid <= floor:
                reached = True
                continue
            if ts >= until_ms:
                continue  # garde-fou (le seek type=2 est deja strictement < borne)
            rows.append((tid, ts, float(x["px"]), float(x["sz"]) * ctval,
                         "buy" if x["side"] == "buy" else "sell"))
        if rows:
            total += sink.insert_rows(rows)
        if reached or page_min_id is None:
            break
        after = str(page_min_id)  # page suivante = plus ancienne
        if pages % 200 == 0:
            print(f"    REST : {pages} pages, +{total:,} trades (id courant {page_min_id})",
                  flush=True)
        time.sleep(pause)
    c.execute("DELETE FROM _meta WHERE k='rest_fill_floor'")
    c.commit()
    return total


# --------------------------------------------------------------------------- #
# Producteur (telechargement + CRC zip) -> file bornee -> consommateur (ingest)
# --------------------------------------------------------------------------- #
def producer(plan: list[Item], cache: str, q: "queue.Queue", stop: threading.Event) -> None:
    for it in plan:
        if stop.is_set():
            break
        dest = os.path.join(cache, it.url.rsplit("/", 1)[-1])
        try:
            if not (os.path.exists(dest) and verify_zip(dest)):
                download_file(it.url, dest)
                if not verify_zip(dest):
                    download_file(it.url, dest)  # un retelechargement avant d'abandonner
                    if not verify_zip(dest):
                        raise RuntimeError("zip corrompu (CRC)")
            q.put((it, dest))
        except NotFound:
            q.put(("MISSING", it.period))  # jour non publie : on n'arrete pas le plan
        except Exception as exc:  # noqa: BLE001
            q.put(("ERROR", f"{it.name}: {exc}"))
            return
    q.put((None, None))  # sentinelle de fin


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def run(args: argparse.Namespace) -> int:
    symbol = args.symbol.upper()
    instid = inst_id(symbol)
    print(f"== OKX history — {symbol} ({instid}) ==")
    ctval = fetch_ctval(instid)
    print(f"ctVal = {ctval} (size en contrats x ctVal = actif de base)")

    plan = build_plan(instid, args.start, args.end)
    if not plan:
        print("Plan vide (aucune periode dans la fenetre).")
        return 0

    if args.format == "sqlite":
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-okx-perp-api.db")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        sink: SqliteSink | CsvSink = SqliteSink(out, symbol, ctval)
    else:
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-okx-perp-trades")
        sink = CsvSink(out, symbol)

    done = sink.done()
    todo = [it for it in plan if it.name not in done]

    span = f"{plan[0].period} … {plan[-1].period}"
    print(f"Plan : {len(plan)} jours ({span}) ; deja fait : "
          f"{len(done & {p.name for p in plan})} ; a traiter : {len(todo)}")
    print(f"Sortie : {out}  (format={args.format})")

    if args.dry_run:
        for it in todo[:10]:
            print("  -", it.name, "->", it.url)
        if len(todo) > 10:
            print(f"  … (+{len(todo) - 10} autres)")
        if args.rest_until:
            print(f"REST tail prevu jusqu'a {args.rest_until} UTC (apres archives).")
        print("DRY-RUN : rien telecharge.")
        sink.close()
        return 0

    def finalize_if_complete() -> None:
        if [it for it in plan if it.name not in sink.done()]:
            return  # plan non termine : index construit au run qui le complete
        if not sink.has_ts_index():
            print("Plan complet -> construction de l'index ts (differe)…")
            t = time.monotonic()
            sink.ensure_ts_index()
            print(f"  index ts OK ({time.monotonic() - t:.0f}s)")

    if not todo:
        print("Archives : deja a jour.")
    else:
        os.makedirs(args.cache, exist_ok=True)
        q: "queue.Queue" = queue.Queue(maxsize=max(1, args.prefetch))
        stop = threading.Event()
        th = threading.Thread(target=producer, args=(todo, args.cache, q, stop),
                              name="downloader", daemon=True)
        th.start()

        t0 = time.monotonic()
        total_rows = total_new = 0
        i = 0
        missing: list[str] = []
        try:
            while True:
                it, dest = q.get()
                if it is None:
                    break
                if it == "MISSING":
                    missing.append(dest)
                    print(f"       (jour non publie : {dest} — sera comble par le REST)")
                    continue
                if it == "ERROR":
                    print(f"\n!! Echec telechargement {dest}. Relance pour reprendre.")
                    break
                i += 1
                r, n = sink.ingest(it, dest, ctval)
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
        print(f"Archives : +{human(total_new)} trades en {time.monotonic() - t0:.0f}s.")
        if missing:
            print(f"Jours non publies au CDN : {', '.join(missing)}")

    # --- queue REST (optionnelle) ---
    if args.rest_until and isinstance(sink, SqliteSink):
        until = parse_day(args.rest_until, last=False)
        until_ms = int(dt.datetime(until.year, until.month, until.day,
                                   tzinfo=dt.timezone.utc).timestamp() * 1000)
        print(f"REST tail -> comblement jusqu'a {until.isoformat()} 00:00 UTC "
              f"(id max en base {sink.max_id()})…")
        t = time.monotonic()
        added = rest_fill(sink, instid, ctval, until_ms)
        print(f"  REST : +{human(added)} trades ({time.monotonic() - t:.0f}s).")
    elif args.rest_until:
        print("(--rest-until ignore hors format sqlite.)")

    finalize_if_complete()

    if isinstance(sink, SqliteSink):
        lo = sink.conn.execute("SELECT min(ts), max(ts), count(*) FROM trades").fetchone()
        if lo and lo[2]:
            def iso(ms): return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()
            print(f"Base : {human(lo[2])} trades, {iso(lo[0])} → {iso(lo[1])}")
    sink.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extracteur autonome d'historique de trades OKX (CDN public + REST).")
    p.add_argument("--symbol", default="BTCUSDT", help="symbole commun, defaut BTCUSDT (-> BTC-USDT-SWAP)")
    p.add_argument("--start", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (defaut: 2021-01-01)")
    p.add_argument("--end", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (defaut: aujourd'hui)")
    p.add_argument("--rest-until", default=None,
                   help="apres les archives, combler la queue via le REST jusqu'a cette date UTC "
                        "(00:00 exclusif ; ex. 2026-07-12) — sqlite seulement")
    p.add_argument("--format", default="sqlite", choices=["sqlite", "csv"])
    p.add_argument("--out", default=None,
                   help="fichier .db (sqlite) ou dossier (csv) ; defaut : data\\<SYMBOLE>-okx-perp-api.db")
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
