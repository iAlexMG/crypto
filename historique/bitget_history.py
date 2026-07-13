#!/usr/bin/env python3
r"""Extracteur d'historique de trades Bitget Futures — AUTONOME (stdlib seule).

Cinquieme venue du montage (pendant de `binance_history.py` / `bybit_history.py` /
`okx_history.py` / `kucoin_history.py` : un seul fichier, stdlib pure, zero import
du projet, copiable tel quel). Normalise au schema commun du projet (ts ms UTC,
prix, size en ACTIF DE BASE, side = cote agresseur).

POURQUOI archives seules (pas de queue REST) ?
  Bitget publie un portail officiel « History data download »
  (bitget.com/data-download). Le bouton du site appelle un endpoint public :
    POST https://www.bitget.com/v1/statistics/public/download/getPublicDataV2
    corps JSON {displaySymbol:["BTCUSDT"], businessLine:2, businessType:2,
                dateType:1, beginTimeStr:"AAAA-MM-JJ", endTimeStr:"AAAA-MM-JJ"}
    (businessLine 1=spot 2=futures ; businessType 1=chandelles 2=trades 3=depth ;
     dateType 1=Day ; plage max 7 jours par appel — MAX_DAY_RANGE du site — et
     dates presentees depuis 2018-01-01 cote UI)
  qui rend la liste des fichiers reellement publies :
    https://img.bitgetimg.com/online/trades/UMCBL/<SYM>/<AAAAMMJJ>_<NNN>.zip
  (UMCBL = perp margine USDT, plusieurs parties de 100 000 lignes par jour,
  profondeur verifiee >= 2023, publication ~1 jour de retard). Le REST public
  /api/v2/mix/market/fills-history est limite aux 90 derniers jours — inutile
  ici puisque les archives couvrent les jours pleins.

PARTICULARITES BITGET (mesurees sur le fichier temoin 20260701) :
  - >> PIEGE UTC+8 (identique a OKX) : le fichier « JJ » couvre
     [JJ-1 16:00, JJ 16:00) UTC — mesure : parties 001..015 du 20260701 =
     06-30 16:00:00 -> 07-01 15:59:57 UTC. Couvrir la fenetre UTC
     [start, end 24:00) demande donc les fichiers start .. end+1, et les lignes
     hors fenetre sont filtrees a l'ingestion ;
  - archive = ZIP -> 1 CSV entete `trade_id,timestamp,price,side,volume(quote),
    size(base)` (100 000 lignes par partie, ~1,4 M trades/jour BTCUSDT) ;
  - `size(base)` est DEJA en actif de base (BTC) — pas de multiplier, contraire-
    ment a OKX (ctVal 0,01) et KuCoin (0,001). Verifie : price*size = volume
    (quote) ligne a ligne sur le temoin. NB l'aide du site inverse les libelles
    des deux colonnes (volume(quote) etiquete « Base currency volume ») — c'est
    l'ENTETE DU CSV qui dit vrai ;
  - `trade_id` : entier 64 bits croissant sur le temoin, MAIS non contigu et non
    garanti cle d'ordre -> meme prudence que KuCoin : id IGNORE, trade_id = rowid
    d'insertion, UNE transaction par partie (interrompu = rollback, jamais de
    doublon), flux verifie chronologique (repli : tri stable), backfill refuse ;
  - `side` observe en minuscules (buy/sell) — normalise .lower() par prudence
    (KuCoin melange 4 casses) ;
  - pas de fichier checksum publie -> verification par CRC des membres du zip ;
  - le listing (getPublicDataV2) repond 429 si appels rapproches -> appels par
    tranches de 7 jours espaces de ~1,2 s, retries avec recul exponentiel.

SORTIE : SQLite (defaut) `data\<SYMBOLE>-bitget-perp-api.db` — nommage standard
  `<SYMBOLE>-<exchange>-<marche>-<source>.db` — schema identique aux autres voies
  (`trades`, `_meta`, `_ingested`, index ts differe) ; ou CSV gzip normalise.

EXEMPLES :
  # voir le plan sans rien telecharger
  python bitget_history.py --start 2026-06-01 --end 2026-07-11 --dry-run

  # la cible du projet : fenetre commune des pages -> data\BTCUSDT-bitget-perp-api.db
  python bitget_history.py --start 2026-06-01 --end 2026-07-11

  # une fenetre precise -> CSV gzip
  python bitget_history.py --start 2026-06-01 --end 2026-06-07 --format csv --out ./csv_out
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
import re
import sqlite3
import sys
import threading
import time
import urllib.request
import zipfile

# --------------------------------------------------------------------------- #
# Constantes / endpoints
# --------------------------------------------------------------------------- #
LISTING = "https://www.bitget.com/v1/statistics/public/download/getPublicDataV2"
PRODUCT = "UMCBL"  # perp margine USDT (nommage v1 de Bitget, present dans les URLs)

# Le CDN et le listing refusent l'UA urllib par defaut (403/429) -> UA navigateur.
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}

HEADER = ["trade_id", "timestamp", "price", "side", "volume(quote)", "size(base)"]

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BATCH = 50_000  # lignes par executemany

# Le fichier « JJ » commence a JJ 00:00 UTC+8 = JJ-1 16:00 UTC = JJ 00:00 UTC - 8 h.
FILE_START_OFFSET = 8 * 3600 * 1000


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


def http_post_json(url: str, body: dict, retries: int = 6, timeout: int = 60) -> dict:
    """POST JSON -> JSON ; recul exponentiel (le listing repond 429 si presse)."""
    payload = json.dumps(body).encode()
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, data=payload, headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(3 * 2 ** i, 60))
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


def verify_zip(path: str) -> bool:
    """CRC de chaque membre (Bitget ne publie pas de somme de controle)."""
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except (zipfile.BadZipFile, OSError, EOFError):
        return False


# --------------------------------------------------------------------------- #
# Plan de telechargement — DEPUIS le listing officiel (getPublicDataV2)
# --------------------------------------------------------------------------- #
class Item:
    __slots__ = ("name", "url", "period", "part")

    def __init__(self, name: str, url: str, period: str, part: int) -> None:
        self.name = name      # ex. "daily/2026-07-01#003"
        self.url = url
        self.period = period  # "2026-07-01" (jour d'ARCHIVE, borne UTC+8)
        self.part = part


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


PART_RE = re.compile(r"_(\d{3})\.zip$")


def build_plan(symbol: str, d0: dt.date, d1_arch: dt.date) -> list[Item]:
    """Interroge le listing par tranches de 7 jours (sa plage max) et ordonne
    jour puis partie. `d1_arch` = dernier jour d'ARCHIVE demande (= end UTC + 1,
    piege UTC+8)."""
    plan: list[Item] = []
    day = d0
    while day <= d1_arch:
        hi = min(day + dt.timedelta(days=6), d1_arch)
        resp = http_post_json(LISTING, {
            "displaySymbol": [symbol], "businessLine": 2, "businessType": 2,
            "dateType": 1, "beginTimeStr": day.isoformat(), "endTimeStr": hi.isoformat()})
        if str(resp.get("code")) != "200":
            raise SystemExit(f"Listing en echec sur {day}..{hi} : {resp}")
        for f in resp.get("data") or []:
            m = PART_RE.search(f["fileName"])
            if not m or f.get("displayName") != symbol:
                continue
            period = f["dateTimeStr"]
            plan.append(Item(f"daily/{period}#{m.group(1)}", f["fileUrl"],
                             period, int(m.group(1))))
        day = hi + dt.timedelta(days=1)
        if day <= d1_arch:
            time.sleep(1.2)  # le listing repond 429 si appels rapproches
    plan.sort(key=lambda it: (it.period, it.part))
    days_seen = {it.period for it in plan}
    want = {(d0 + dt.timedelta(days=i)).isoformat()
            for i in range((d1_arch - d0).days + 1)}
    missing = sorted(want - days_seen)
    if missing:
        print(f"ATTENTION : {len(missing)} jour(s) d'archive absents du listing "
              f"(publication ~1 jour de retard) : {', '.join(missing[:5])}"
              f"{' …' if len(missing) > 5 else ''}")
    return plan


# --------------------------------------------------------------------------- #
# Parsing d'un zip Bitget -> (ts_ms, price, size_base, side)
# Entete : trade_id,timestamp,price,side,volume(quote),size(base). L'id est
# IGNORE (non contigu, cle d'ordre non garantie — voir docstring). size(base)
# deja en actif de base : PAS de multiplier. Lignes hors [t0, t1) filtrees
# (fichiers bornes UTC+8, fenetre du projet bornee UTC).
# --------------------------------------------------------------------------- #
def iter_rows(zip_path: str, t0: int | None, t1: int | None):
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        with z.open(name) as raw:
            rd = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            first = next(rd, None)
            if first and first[:1] and first[0] != HEADER[0]:
                raise SystemExit(f"Entete inattendue dans {name} : {first} "
                                 f"(attendu {HEADER}) — format change, verifier.")
            for r in rd:
                if not r:
                    continue
                ts = int(r[1])
                if (t0 is not None and ts < t0) or (t1 is not None and ts >= t1):
                    continue
                yield (ts, float(r[2]), float(r[5]),
                       "buy" if r[3].lower() == "buy" else "sell")


# --------------------------------------------------------------------------- #
# Ecrivains : SQLite et CSV gzip
# --------------------------------------------------------------------------- #
class SqliteSink:
    def __init__(self, path: str, symbol: str) -> None:
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
            for k, v in (("symbol", symbol), ("market", "swap"), ("exchange", "bitget"),
                         ("contract_val", "1"), ("source", "bitget-public-archives")):
                c.execute("INSERT OR REPLACE INTO _meta VALUES(?,?)", (k, v))
        c.commit()

    def done(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM _ingested")}

    def max_ts(self) -> int | None:
        r = self.conn.execute("SELECT max(ts) FROM trades").fetchone()
        return r[0] if r and r[0] is not None else None

    def ingest(self, item: Item, zip_path: str, t0: int | None, t1: int | None) -> int:
        """UNE transaction par partie (donnees + marqueur `_ingested`) : sans id de
        dedup, l'atomicite remplace l'INSERT OR IGNORE. Le flux est insere tel quel
        s'il est chronologique (cas normal mesure) ; a la premiere inversion,
        rollback et seconde passe TRIEE (tri stable : a ts egal, ordre du fichier)."""
        c = self.conn
        try:
            rows = self._insert_streaming(item, zip_path, t0, t1)
        except _OutOfOrder:
            c.rollback()
            print(f"    (flux non chronologique -> tri de {item.name})")
            rows = self._insert_sorted(item, zip_path, t0, t1)
        c.commit()  # data + marqueur de reprise = meme transaction
        return rows

    def _insert_streaming(self, item: Item, zip_path: str,
                          t0: int | None, t1: int | None) -> int:
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        rows = 0
        prev = -1
        batch: list[tuple] = []
        for ts, price, size, side in iter_rows(zip_path, t0, t1):
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

    def _insert_sorted(self, item: Item, zip_path: str,
                       t0: int | None, t1: int | None) -> int:
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        # tri STABLE sur ts seul : a ts egal, l'ordre du fichier (= ordre exchange) prime
        data = sorted(iter_rows(zip_path, t0, t1), key=lambda r: r[0])
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
    """Un .csv.gz normalise par PARTIE d'archive. Reprise = presence du fichier."""
    def __init__(self, out_dir: str, symbol: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        self.dir = out_dir
        self.symbol = symbol

    def _path(self, item: Item) -> str:
        return os.path.join(
            self.dir, f"{self.symbol}-bitget-trades-{item.period}-{item.part:03d}.csv.gz")

    def done(self) -> set[str]:
        names: set[str] = set()
        pat = re.compile(rf"^{re.escape(self.symbol)}-bitget-trades-"
                         rf"(\d{{4}}-\d{{2}}-\d{{2}})-(\d{{3}})\.csv\.gz$")
        for fn in os.listdir(self.dir) if os.path.isdir(self.dir) else []:
            m = pat.match(fn)
            if m:
                names.add(f"daily/{m.group(1)}#{m.group(2)}")
        return names

    def max_ts(self) -> int | None:
        return None  # fichiers independants : pas de contrainte d'ordre global

    def ingest(self, item: Item, zip_path: str, t0: int | None, t1: int | None) -> int:
        final = self._path(item)
        tmp = final + ".part"
        rows = 0
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
            w = csv.writer(gz)
            w.writerow(["ts", "price", "size", "side"])
            for ts, price, size, side in sorted(iter_rows(zip_path, t0, t1),
                                                key=lambda r: r[0]):
                w.writerow([ts, price, size, side])
                rows += 1
        os.replace(tmp, final)
        return rows

    def has_ts_index(self) -> bool:
        return True

    def ensure_ts_index(self) -> None:
        pass

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Producteur (telechargement + CRC) -> file bornee -> consommateur (ingest)
# --------------------------------------------------------------------------- #
def producer(plan: list[Item], cache: str, q: "queue.Queue", stop: threading.Event) -> None:
    for it in plan:
        if stop.is_set():
            break
        dest = os.path.join(cache, f"{it.period}_{it.part:03d}.zip")
        try:
            if not (os.path.exists(dest) and verify_zip(dest)):
                download_file(it.url, dest)
                if not verify_zip(dest):
                    download_file(it.url, dest)  # un retelechargement avant d'abandonner
                    if not verify_zip(dest):
                        raise RuntimeError("zip corrompu (CRC)")
            q.put((it, dest))
        except Exception as exc:  # noqa: BLE001
            q.put(("ERROR", f"{it.name}: {exc}"))
            return
    q.put((None, None))  # sentinelle de fin


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def run(args: argparse.Namespace) -> int:
    symbol = args.symbol.upper()
    print(f"== Bitget history — {symbol} (archives {PRODUCT}, bornees UTC+8) ==")

    d0 = parse_day(args.start, last=False)
    d1 = parse_day(args.end, last=True)
    if d0 is None or d1 is None:
        raise SystemExit("--start et --end sont requis (le listing s'interroge par "
                         "tranches de 7 jours ; pas de « tout depuis le debut »).")
    # Fenetre du projet en UTC ; fichiers bornes UTC+8 -> il faut end+1 en archives,
    # et un filtre de lignes [start 00:00, end+1 00:00) UTC a l'ingestion.
    t0 = int(dt.datetime(d0.year, d0.month, d0.day, tzinfo=dt.timezone.utc)
             .timestamp() * 1000)
    d1x = d1 + dt.timedelta(days=1)
    t1 = int(dt.datetime(d1x.year, d1x.month, d1x.day, tzinfo=dt.timezone.utc)
             .timestamp() * 1000)

    print(f"Fenetre UTC : {d0} 00:00 -> {d1x} 00:00 "
          f"(archives {d0} .. {d1x}, piege UTC+8)")
    print("Construction du plan (listing getPublicDataV2, tranches de 7 jours)…")
    plan = build_plan(symbol, d0, d1x)
    if not plan:
        print("Plan vide (aucune archive publiee dans la fenetre).")
        return 0

    if args.format == "sqlite":
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-bitget-perp-api.db")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        sink: SqliteSink | CsvSink = SqliteSink(out, symbol)
    else:
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-bitget-perp-trades")
        sink = CsvSink(out, symbol)

    done = sink.done()
    todo = [it for it in plan if it.name not in done]

    # Garde-fou chronologie (hypothese candles.py : rowid = ordre chrono) : sans id de
    # trade utilisable, un backfill AVANT le contenu existant casserait l'ordre des
    # rowids. Une reprise est saine ssi les parties deja ingerees forment un PREFIXE
    # du plan (les parties sont ordonnees jour puis numero) ; sinon, ou si le plan ne
    # recouvre pas du tout la base existante (fenetre deplacee), on refuse.
    if todo and (mx := sink.max_ts()) is not None:
        done_flags = [it.name in done for it in plan]
        k = done_flags.index(False)              # premiere partie manquante
        if any(done_flags[k:]):
            print("REFUS : les parties deja ingerees ne forment pas un prefixe du plan "
                  "(trou au milieu) — l'ordre chronologique des rowids n'est plus "
                  "garantissable.\n-> reconstruire la base sur la fenetre complete "
                  "(ou utiliser --out vers une nouvelle base).")
            sink.close()
            return 1
        if k == 0:
            # rien du plan n'est ingere mais la base n'est pas vide : la fenetre a change
            file_start = int(dt.datetime.fromisoformat(todo[0].period)
                             .replace(tzinfo=dt.timezone.utc).timestamp() * 1000) \
                - FILE_START_OFFSET
            if file_start <= mx:
                print(f"REFUS : la base contient deja des trades jusqu'a "
                      f"{dt.datetime.fromtimestamp(mx / 1000, dt.timezone.utc)} et le plan "
                      f"demarre a {todo[0].name} (fichier couvrant des lors) : backfill.\n"
                      f"-> reconstruire la base sur la fenetre complete "
                      f"(ou utiliser --out vers une nouvelle base).")
                sink.close()
                return 1

    span = f"{plan[0].period} … {plan[-1].period}"
    print(f"Plan : {len(plan)} parties ({span}) ; deja fait : "
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

    t0_run = time.monotonic()
    total_rows = 0
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
            r = sink.ingest(it, dest, t0, t1)
            total_rows += r
            if not args.keep_zip:
                try:
                    os.remove(dest)
                except OSError:
                    pass
            el = time.monotonic() - t0_run
            rate = int(total_rows / el) if el > 0 else 0
            print(f"[{i:>4}/{len(todo)}] {it.name:<22} "
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
    print(f"Termine : +{human(total_rows)} trades en {time.monotonic() - t0_run:.0f}s.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extracteur autonome d'historique de trades Bitget Futures "
                    "(portail public bitget.com/data-download).")
    p.add_argument("--symbol", default="BTCUSDT",
                   help="symbole commun, defaut BTCUSDT (produit UMCBL = perp USDT)")
    p.add_argument("--start", required=False, default=None,
                   help="YYYY | YYYY-MM | YYYY-MM-DD (REQUIS)")
    p.add_argument("--end", required=False, default=None,
                   help="YYYY | YYYY-MM | YYYY-MM-DD (REQUIS)")
    p.add_argument("--format", default="sqlite", choices=["sqlite", "csv"])
    p.add_argument("--out", default=None,
                   help="fichier .db (sqlite) ou dossier (csv) ; defaut : data\\<SYMBOLE>-bitget-perp-api.db")
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
