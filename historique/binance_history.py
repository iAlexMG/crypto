#!/usr/bin/env python3
"""Extracteur d'historique de trades Binance — AUTONOME (stdlib seule).

Reprend la logique d'extraction du projet Crypto (trades tick-par-tick normalisés
au format Bitget : ts ms UTC, prix, size en actif de base, side = côté agresseur),
mais en VERSION INDÉPENDANTE : aucun import du projet, aucune dépendance externe
(urllib, zipfile, csv, sqlite3, hashlib, argparse — tout est dans la stdlib).
Copiable tel quel dans un autre sous-projet.

POURQUOI les dumps et pas le REST page-par-page ?
  Le collecteur du projet pagine les REST (fetch_before / fetch_range). Pour « le
  plus long possible » (BTCUSDT perp depuis fin 2019 = des centaines de millions de
  trades), cela voudrait dire des millions de requêtes. Binance publie les MÊMES
  trades agrégés en archives mensuelles/quotidiennes sur data.binance.vision : on
  télécharge ~100 fichiers au lieu de paginer des millions de fois. Même donnée,
  même normalisation. Option `--rest-tail` pour compléter le dernier jour via le REST
  (exactement le pattern fetch_since du projet).

COUVERTURE (BTCUSDT, USDⓈ-M / `um`) :
  - dumps disponibles à partir du 2019-12-31 (daily) ; mensuels dès 2020-01 ;
  - mensuels pour les mois complets, quotidiens pour le 1er mois partiel et le mois
    courant (le plan est construit DEPUIS le listing S3 → chaque URL planifiée existe).

SORTIE :
  - SQLite (défaut) : table `trades(trade_id PK, ts, price, size, side)`, dédup par
    INSERT OR IGNORE, WAL ; identique à l'esprit de `data/trades.db` du projet ;
  - CSV gzip (--format csv) : un fichier normalisé par période (ts,price,size,side,trade_id).

REPRISE : chaque fichier ingéré est marqué (table `_ingested` en SQLite, ou présence
  du .csv.gz en CSV). Relancer la commande REPREND où ça s'est arrêté. Ctrl-C entre
  deux fichiers est sûr ; coupé en plein fichier, il sera re-traité (INSERT OR IGNORE).

EXEMPLES :
  # tout l'historique BTCUSDT perp en SQLite (peut durer longtemps + dizaines de Go)
  python binance_history.py

  # voir le plan sans rien télécharger
  python binance_history.py --dry-run

  # une fenêtre précise, vers du CSV gzip
  python binance_history.py --start 2024-01 --end 2024-03 --format csv --out ./csv_out

  # compléter jusqu'à maintenant via le REST après les dumps
  python binance_history.py --rest-tail
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
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

# --------------------------------------------------------------------------- #
# Constantes / endpoints
# --------------------------------------------------------------------------- #
VISION = "https://data.binance.vision"
S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

# Dossier de données du pilier, ancré sur le script (pas le cwd) : historique\data\,
# gitignoré à la racine. Remplace F:\data depuis le rapatriement dans le dépôt (2026-07).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
UA = {"User-Agent": "Mozilla/5.0 (binance-history-extractor)"}

# Marché -> (segment de chemin sur data.binance.vision, base REST, chemin aggTrades REST).
# REST None = pas de tail REST simple pour ce marché (cm = endpoint dapi distinct).
MARKETS = {
    "um":   ("data/futures/um", "https://fapi.binance.com", "/fapi/v1/aggTrades"),
    "cm":   ("data/futures/cm", None, None),
    "spot": ("data/spot",       "https://api.binance.com", "/api/v3/aggTrades"),
}

BATCH = 50_000          # lignes par executemany / par flush gzip
HOLE_LOG_EVERY = 5_000_000


# --------------------------------------------------------------------------- #
# HTTP (avec retries) + vérification SHA256
# --------------------------------------------------------------------------- #
def http_get(url: str, retries: int = 5, timeout: int = 60) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 - réseau, on réessaie
            last = exc
            time.sleep(min(2 ** i, 30))
    raise last  # type: ignore[misc]


def download_file(url: str, dest: str, retries: int = 5, timeout: int = 120) -> None:
    """Télécharge en streaming vers `dest` (.part puis rename, pour atomicité)."""
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


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(zip_path: str, zip_url: str) -> bool:
    """Compare le SHA256 du zip au .CHECKSUM publié ; True si OK (ou indisponible)."""
    try:
        text = http_get(zip_url + ".CHECKSUM").decode()
    except Exception:  # noqa: BLE001 - pas de checksum -> on ne bloque pas
        return True
    expected = text.split()[0].strip().lower() if text.split() else ""
    return bool(expected) and sha256_file(zip_path).lower() == expected


# --------------------------------------------------------------------------- #
# Listing S3 (pagination par marker)
# --------------------------------------------------------------------------- #
def s3_list_zips(prefix: str) -> list[str]:
    """Toutes les clés .zip sous `prefix` (suit la pagination IsTruncated/NextMarker)."""
    keys: list[str] = []
    marker = ""
    while True:
        url = f"{S3_LIST}?delimiter=/&prefix={urllib.parse.quote(prefix)}&max-keys=1000"
        if marker:
            url += "&marker=" + urllib.parse.quote(marker)
        root = ET.fromstring(http_get(url).decode())
        page = [k.text or "" for k in root.findall(".//s3:Contents/s3:Key", S3_NS)]
        keys.extend(page)
        truncated = root.findtext(".//s3:IsTruncated", "false", S3_NS) == "true"
        if not truncated:
            break
        marker = root.findtext(".//s3:NextMarker", "", S3_NS) or (page[-1] if page else "")
        if not marker:
            break
    return [k for k in keys if k.endswith(".zip")]


# --------------------------------------------------------------------------- #
# Dates / périodes (arithmétique en mois = year*12 + (month-1))
# --------------------------------------------------------------------------- #
def ym_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def index_ym(idx: int) -> tuple[int, int]:
    return idx // 12, idx % 12 + 1


def parse_period(s: str | None) -> tuple[int | None, int | None]:
    """'YYYY' | 'YYYY-MM' | 'YYYY-MM-DD' -> (ym_index, day|None). None -> (None, None)."""
    if not s:
        return None, None
    parts = s.split("-")
    y = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 1
    d = int(parts[2]) if len(parts) > 2 else None
    return ym_index(y, m), d


# --------------------------------------------------------------------------- #
# Construction du plan de téléchargement (mensuel + quotidien)
# --------------------------------------------------------------------------- #
class Item:
    __slots__ = ("name", "url", "kind", "period")

    def __init__(self, name: str, url: str, kind: str, period: str) -> None:
        self.name = name      # ex. "monthly/2020-01" ou "daily/2019-12-31"
        self.url = url
        self.kind = kind      # "monthly" | "daily"
        self.period = period  # "2020-01" | "2019-12-31"


def build_plan(market: str, symbol: str, start: str | None, end: str | None) -> list[Item]:
    seg = MARKETS[market][0]
    root = f"{seg}/monthly/aggTrades/{symbol}/"
    # 1) mois disponibles en MENSUEL
    monthly_keys = s3_list_zips(root)
    monthly_set: set[str] = set()
    for k in monthly_keys:
        stem = k.rsplit("/", 1)[-1].removeprefix(f"{symbol}-aggTrades-").removesuffix(".zip")
        if len(stem) == 7:  # YYYY-MM
            monthly_set.add(stem)
    if not monthly_set:
        raise SystemExit(f"Aucun dump mensuel trouvé pour {symbol} ({market}). "
                         f"Vérifie le symbole/marché.")

    earliest_month = min(monthly_set)
    ey, em = (int(earliest_month[:4]), int(earliest_month[5:7]))
    # défaut start = mois AVANT le 1er mensuel (capte le 1er mois partiel en daily)
    default_start = ym_index(ey, em) - 1
    today = dt.datetime.now(dt.timezone.utc).date()
    default_end = ym_index(today.year, today.month)

    start_ym, start_day = parse_period(start)
    end_ym, end_day = parse_period(end)
    if start_ym is None:
        start_ym = default_start
    if end_ym is None:
        end_ym = default_end

    plan: list[Item] = []
    daily_cache: dict[str, list[str]] = {}

    def daily_dates_for_month(ym: int) -> list[str]:
        y, m = index_ym(ym)
        key = f"{y:04d}-{m:02d}"
        if key not in daily_cache:
            pfx = f"{seg}/daily/aggTrades/{symbol}/{symbol}-aggTrades-{key}"
            keys = s3_list_zips(pfx)
            dates = sorted(
                k.rsplit("/", 1)[-1].removeprefix(f"{symbol}-aggTrades-").removesuffix(".zip")
                for k in keys
            )
            daily_cache[key] = [d for d in dates if len(d) == 10]
        return daily_cache[key]

    for ym in range(start_ym, end_ym + 1):
        y, m = index_ym(ym)
        yms = f"{y:04d}-{m:02d}"
        is_boundary = ((ym == start_ym and start_day is not None) or
                       (ym == end_ym and end_day is not None))
        if yms in monthly_set and not is_boundary:
            url = f"{VISION}/{seg}/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{yms}.zip"
            plan.append(Item(f"monthly/{yms}", url, "monthly", yms))
        else:
            # quotidien : mois sans mensuel, mois courant, ou bord de fenêtre jour-précis
            for d in daily_dates_for_month(ym):
                dd = int(d[8:10])
                if ym == start_ym and start_day is not None and dd < start_day:
                    continue
                if ym == end_ym and end_day is not None and dd > end_day:
                    continue
                url = f"{VISION}/{seg}/daily/aggTrades/{symbol}/{symbol}-aggTrades-{d}.zip"
                plan.append(Item(f"daily/{d}", url, "daily", d))
    return plan


# --------------------------------------------------------------------------- #
# Parsing CSV d'un zip aggTrades -> (trade_id, ts, price, size, side)
# Colonnes (um/cm/spot) : agg_id, price, qty, first_id, last_id, transact_time, is_buyer_maker[, is_best_match]
# is_buyer_maker=true -> agresseur = VENDEUR -> side="sell" (comme le projet).
# Entête éventuel (fichiers récents) détecté/ignoré : 1re colonne non numérique.
# --------------------------------------------------------------------------- #
def iter_rows(zip_path: str):
    z = zipfile.ZipFile(zip_path)
    name = z.namelist()[0]
    with z.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        for r in csv.reader(text):
            if not r:
                continue
            try:
                tid = int(r[0])
            except ValueError:
                continue  # ligne d'entête
            ts = int(r[5])
            price = float(r[1])
            size = float(r[2])
            side = "sell" if r[6].strip().lower() in ("true", "1") else "buy"
            yield tid, ts, price, size, side


# --------------------------------------------------------------------------- #
# Écrivains : SQLite et CSV gzip
# --------------------------------------------------------------------------- #
class SqliteSink:
    def __init__(self, path: str, market: str, symbol: str) -> None:
        first = not os.path.exists(path)
        self.conn = sqlite3.connect(path)
        c = self.conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-1048576")  # ~1 Go de cache page (insertion + build index)
        c.execute("""CREATE TABLE IF NOT EXISTS trades(
                        trade_id INTEGER PRIMARY KEY,   -- = rowid : insertion en APPEND (ids croissants)
                        ts       INTEGER NOT NULL,
                        price    REAL    NOT NULL,
                        size     REAL    NOT NULL,
                        side     TEXT    NOT NULL)""")
        # INDEX ts DIFFÉRÉ : PAS créé pendant l'ingestion (sinon un 2e B-tree à maintenir
        # à chaque insert sur des milliards de lignes). Construit en UNE fois à la fin du
        # plan complet (ensure_ts_index) -> insertion = simple append sur le rowid. Cf. run().
        c.execute("""CREATE TABLE IF NOT EXISTS _ingested(
                        name TEXT PRIMARY KEY, rows INTEGER, at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS _meta(k TEXT PRIMARY KEY, v TEXT)""")
        if first:
            c.execute("INSERT OR REPLACE INTO _meta VALUES('symbol',?)", (symbol,))
            c.execute("INSERT OR REPLACE INTO _meta VALUES('market',?)", (market,))
        c.commit()

    def done(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM _ingested")}

    def ingest(self, item: Item, zip_path: str) -> tuple[int, int]:
        c = self.conn
        before = c.total_changes
        rows = 0
        cur = c.cursor()
        batch: list[tuple] = []
        for row in iter_rows(zip_path):
            batch.append(row)
            if len(batch) >= BATCH:
                cur.executemany(
                    "INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?)", batch)
                rows += len(batch)
                batch.clear()
        if batch:
            cur.executemany("INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?)", batch)
            rows += len(batch)
        new = c.total_changes - before          # trades réellement insérés (avant le marqueur)
        c.execute("INSERT OR REPLACE INTO _ingested VALUES(?,?,?)",
                  (item.name, rows, dt.datetime.now(dt.timezone.utc).isoformat()))
        c.commit()  # data + marqueur de reprise = même transaction
        return rows, new

    def last_ts(self) -> int | None:
        r = self.conn.execute("SELECT max(ts) FROM trades").fetchone()
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
        """Construit l'index ts différé s'il manque (1 seule fois, plan complet)."""
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class CsvSink:
    """Un .csv.gz normalisé par période. Reprise = présence du fichier final."""
    def __init__(self, out_dir: str, market: str, symbol: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        self.dir = out_dir
        self.symbol = symbol
        self.market = market

    def _path(self, item: Item) -> str:
        tag = item.period
        return os.path.join(self.dir, f"{self.symbol}-{self.market}-aggTrades-{tag}.csv.gz")

    def done(self) -> set[str]:
        names: set[str] = set()
        for fn in os.listdir(self.dir) if os.path.isdir(self.dir) else []:
            if fn.endswith(".csv.gz"):
                tag = fn.removeprefix(f"{self.symbol}-{self.market}-aggTrades-").removesuffix(".csv.gz")
                kind = "daily" if len(tag) == 10 else "monthly"
                names.add(f"{kind}/{tag}")
        return names

    def ingest(self, item: Item, zip_path: str) -> tuple[int, int]:
        final = self._path(item)
        tmp = final + ".part"
        rows = 0
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
            w = csv.writer(gz)
            w.writerow(["ts", "price", "size", "side", "trade_id"])
            for tid, ts, price, size, side in iter_rows(zip_path):
                w.writerow([ts, price, size, side, tid])
                rows += 1
        os.replace(tmp, final)
        return rows, rows

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# REST tail : complète du dernier ts archivé jusqu'à maintenant (pattern projet)
# --------------------------------------------------------------------------- #
def rest_tail(sink: SqliteSink, market: str, symbol: str, pause: float = 0.2) -> int:
    base, path, _ = MARKETS[market][1], MARKETS[market][2], None
    if base is None:
        print(f"  (--rest-tail non supporté pour le marché '{market}')")
        return 0
    start = sink.last_ts()
    if start is None:
        print("  (rien en base -> pas de tail)")
        return 0
    start += 1
    now = int(time.time() * 1000)
    total = 0
    params = {"symbol": symbol, "startTime": str(start), "limit": "1000"}
    while True:
        url = f"{base}{path}?{urllib.parse.urlencode(params)}"
        page = json.loads(http_get(url).decode())
        if not page:
            break
        rows = [(int(d["a"]), int(d["T"]), float(d["p"]), float(d["q"]),
                 "sell" if d.get("m") else "buy") for d in page]
        total += sink.insert_rows(rows)
        last = page[-1]
        if len(page) < 1000 or int(last["T"]) >= now:
            break
        params = {"symbol": symbol, "fromId": str(int(last["a"]) + 1), "limit": "1000"}
        time.sleep(pause)
    return total


# --------------------------------------------------------------------------- #
# Producteur (téléchargement + vérif) -> file bornée -> consommateur (ingest)
# --------------------------------------------------------------------------- #
def producer(plan: list[Item], cache: str, verify: bool, q: "queue.Queue",
             stop: threading.Event) -> None:
    for it in plan:
        if stop.is_set():
            break
        dest = os.path.join(cache, it.url.rsplit("/", 1)[-1])
        try:
            if not (os.path.exists(dest) and (not verify or verify_checksum(dest, it.url))):
                download_file(it.url, dest)
                if verify and not verify_checksum(dest, it.url):
                    # un retéléchargement avant d'abandonner
                    download_file(it.url, dest)
                    if not verify_checksum(dest, it.url):
                        raise RuntimeError("checksum invalide")
            q.put((it, dest))
        except Exception as exc:  # noqa: BLE001
            q.put(("ERROR", f"{it.name}: {exc}"))
            return
    q.put((None, None))  # sentinelle de fin


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def run(args: argparse.Namespace) -> int:
    market, symbol = args.market, args.symbol.upper()
    print(f"== Binance history — {symbol} [{market}] aggTrades ==")
    print("Construction du plan (listing data.binance.vision)…")
    plan = build_plan(market, symbol, args.start, args.end)
    if not plan:
        print("Plan vide (aucune période dans la fenêtre).")
        return 0

    # Sink (sqlite/csv) + reprise
    if args.format == "sqlite":
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-{market}-api.db")
        # L'index ts différé est construit à la fin : son tri externe (milliards de
        # lignes) a besoin de place TEMP -> sur le MÊME disque que la base (jamais C:).
        tmpdir = os.path.join(os.path.dirname(os.path.abspath(out)) or ".", "_sqlite_tmp")
        os.makedirs(tmpdir, exist_ok=True)
        os.environ["TMP"] = os.environ["TEMP"] = os.environ["SQLITE_TMPDIR"] = tmpdir
        sink: SqliteSink | CsvSink = SqliteSink(out, market, symbol)
    else:
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-{market}-aggtrades")
        sink = CsvSink(out, market, symbol)

    def finalize_if_complete() -> None:
        """Plan entièrement ingéré -> construit l'index ts différé puis (option) tail.
        Si le plan n'est pas fini (interruption/erreur), on NE construit PAS l'index
        (il serait reconstruit à chaque reprise) -> il attend le run qui complète le plan."""
        if not isinstance(sink, SqliteSink):
            return
        if [it for it in plan if it.name not in sink.done()]:
            print("Plan non terminé -> l'index ts sera construit au run qui le complète.")
            return
        if not sink.has_ts_index():
            print("Plan complet -> construction de l'index ts (différé ; plusieurs minutes)…")
            t = time.monotonic()
            sink.ensure_ts_index()
            print(f"  index ts OK ({time.monotonic() - t:.0f}s)")
        if args.rest_tail:
            print("REST tail (dernier dump -> maintenant)…")
            try:
                print(f"  +{human(rest_tail(sink, market, symbol))} trades (tail).")
            except Exception as exc:  # noqa: BLE001
                print(f"  tail échoué (sans gravité) : {exc}")

    done = sink.done()
    todo = [it for it in plan if it.name not in done]

    span = f"{plan[0].period} … {plan[-1].period}"
    print(f"Plan : {len(plan)} fichiers ({span}) ; déjà fait : {len(done & {p.name for p in plan})} ; "
          f"à traiter : {len(todo)}")
    print(f"Sortie : {out}  (format={args.format})")
    if args.newest_first:
        todo.reverse()

    if args.dry_run:
        for it in todo[:10]:
            print("  -", it.name, "->", it.url)
        if len(todo) > 10:
            print(f"  … (+{len(todo) - 10} autres)")
        print("DRY-RUN : rien téléchargé.")
        sink.close()
        return 0

    if not todo:
        print("Tout est déjà à jour.")
        finalize_if_complete()
        sink.close()
        return 0

    os.makedirs(args.cache, exist_ok=True)
    q: "queue.Queue" = queue.Queue(maxsize=max(1, args.prefetch))
    stop = threading.Event()
    th = threading.Thread(target=producer, args=(todo, args.cache, not args.no_verify, q, stop),
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
            if it == "ERROR":                    # échec producteur -> on s'arrête (reprenable)
                print(f"\n!! Échec téléchargement {dest}. Relance la commande pour reprendre.")
                break
            i += 1
            r, n = sink.ingest(it, dest)
            total_rows += r
            total_new += n
            if not args.keep_zips:
                try:
                    os.remove(dest)
                except OSError:
                    pass
            el = time.monotonic() - t0
            rate = int(total_rows / el) if el > 0 else 0
            print(f"[{i:>4}/{len(todo)}] {it.name:<20} "
                  f"{human(r):>14} lignes (+{human(n)} new) | "
                  f"cumul {human(total_rows)} | {human(rate)}/s | {el:6.0f}s")
    except KeyboardInterrupt:
        print("\nInterrompu — état sauvegardé (reprenable en relançant).")
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
    print(f"Terminé : +{human(total_new)} nouveaux trades en {time.monotonic() - t0:.0f}s.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extracteur autonome d'historique de trades Binance (data.binance.vision).")
    p.add_argument("--symbol", default="BTCUSDT", help="défaut BTCUSDT")
    p.add_argument("--market", default="um", choices=list(MARKETS),
                   help="um=USDⓈ-M futures (défaut), cm=COIN-M, spot")
    p.add_argument("--start", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (défaut: début dispo)")
    p.add_argument("--end", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (défaut: mois courant)")
    p.add_argument("--format", default="sqlite", choices=["sqlite", "csv"])
    p.add_argument("--out", default=None,
                   help="fichier .db (sqlite) ou dossier (csv) ; défaut : data\\<SYMBOLE>-<marché>-api.db")
    p.add_argument("--cache", default=os.path.join(DATA_DIR, "_dumps"),
                   help="cache des zips téléchargés (défaut : data\\_dumps)")
    p.add_argument("--prefetch", type=int, default=2, help="zips téléchargés en avance (borne le disque)")
    p.add_argument("--keep-zips", action="store_true", help="garder les zips après ingestion")
    p.add_argument("--no-verify", action="store_true", help="ne pas vérifier les SHA256")
    p.add_argument("--newest-first", action="store_true", help="traiter du plus récent au plus ancien")
    p.add_argument("--rest-tail", action="store_true",
                   help="compléter du dernier dump jusqu'à maintenant via le REST (sqlite, um/spot)")
    p.add_argument("--dry-run", action="store_true", help="afficher le plan sans télécharger")
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
