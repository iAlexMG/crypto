#!/usr/bin/env python3
r"""Extracteur d'historique de trades Bybit — AUTONOME (stdlib seule).

Pendant Bybit de `binance_history.py` (même philosophie : un seul fichier, stdlib
pure, zéro import du projet, copiable tel quel) : télécharge les archives publiques
quotidiennes de public.bybit.com et les normalise au schéma commun du projet
(ts ms UTC, prix, size en actif de base, side = côté agresseur).

POURQUOI les archives et pas le REST ?
  Le REST public de Bybit (recent-trade) est plafonné à 1 000 trades sans pagination
  du passé — inutilisable pour l'historique (constat documenté sur la page Bybit du
  pilier affichage). Mais public.bybit.com publie UN fichier csv.gz par jour et par
  symbole, en libre accès : BTCUSDT (dérivés linéaires) remonte au 2020-03-25.

DIFFÉRENCES STRUCTURELLES avec Binance (mesurées sur fichier témoin 2026-07-10) :
  - colonnes : timestamp,symbol,side,size,price,tickDirection,trdMatchID,
    grossValue,homeNotional,foreignNotional[,RPI] — l'entête est présent et des
    colonnes s'ajoutent avec le temps → parsing par INDEX FIXES 0-4 (stables) ;
  - `timestamp` en SECONDES epoch avec fraction (précision sous-ms) → ms arrondies ;
  - `side` (Buy/Sell) = côté agresseur DIRECT (pas d'inversion is_buyer_maker) ;
  - `size` déjà en actif de base (BTC) — vérifié : homeNotional = size,
    foreignNotional = price × size ;
  - PAS d'identifiant entier (trdMatchID = UUID) → `trade_id` = rowid d'insertion,
    comme la méthode B (Quantower). Conséquences :
      * dédup impossible par id → chaque fichier est ingéré dans UNE transaction
        (données + marqueur `_ingested`) : interrompu = rollback, jamais de doublon ;
      * l'ordre chronologique des rowids est GARANTI : flux vérifié monotone à
        l'ingestion (repli : tri du fichier), et tout backfill AVANT le contenu
        existant de la base est refusé (hypothèse de candles.py : rowid = chrono) ;
  - PAS de .CHECKSUM publié → l'intégrité repose sur le CRC32 du gzip (vérifié en
    décompressant intégralement chaque fichier avant ingestion).

SORTIE : SQLite (défaut) `data\<SYMBOLE>-bybit-api.db` — schéma identique aux autres
  voies (`trades`, `_meta`, `_ingested`, index ts différé) ; ou CSV gzip normalisé.

EXEMPLES :
  # voir le plan sans rien télécharger
  python bybit_history.py --start 2026-06 --dry-run

  # la cible du projet : juin-juillet 2026 -> data\BTCUSDT-bybit-api.db (reprenable)
  python bybit_history.py --start 2026-06

  # une fenêtre précise -> CSV gzip
  python bybit_history.py --start 2026-06-01 --end 2026-06-07 --format csv --out ./csv_out
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import urllib.request

# --------------------------------------------------------------------------- #
# Constantes / endpoints
# --------------------------------------------------------------------------- #
PUBLIC = "https://public.bybit.com"
CATEGORY = "trading"  # dérivés (linéaires pour les paires USDT) ; spot hors périmètre

# Dossier de données du pilier, ancré sur le script (pas le cwd) : historique\data\,
# gitignoré à la racine du dépôt.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UA = {"User-Agent": "Mozilla/5.0 (bybit-history-extractor)"}

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
        except Exception as exc:  # noqa: BLE001 - réseau, on réessaie
            last = exc
            time.sleep(min(2 ** i, 30))
    raise last  # type: ignore[misc]


def download_file(url: str, dest: str, retries: int = 5, timeout: int = 180) -> None:
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


def verify_gzip(path: str) -> bool:
    """Décompresse intégralement le fichier : le CRC32 du gzip fait foi (Bybit ne
    publie pas de somme de contrôle séparée). Faux si tronqué/corrompu."""
    try:
        with gzip.open(path, "rb") as f:
            while f.read(1 << 20):
                pass
        return True
    except (OSError, EOFError, gzip.BadGzipFile):
        return False


# --------------------------------------------------------------------------- #
# Plan de téléchargement — DEPUIS le listing réel (pas de 404 deviné)
# --------------------------------------------------------------------------- #
class Item:
    __slots__ = ("name", "url", "period")

    def __init__(self, name: str, url: str, period: str) -> None:
        self.name = name      # ex. "daily/2026-06-01" (même convention que Binance)
        self.url = url
        self.period = period  # "2026-06-01"


def parse_day(s: str | None, last: bool) -> dt.date | None:
    """'YYYY' | 'YYYY-MM' | 'YYYY-MM-DD' -> date de début (ou de FIN si last=True)."""
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


def build_plan(symbol: str, start: str | None, end: str | None) -> list[Item]:
    """Liste les csv.gz réellement publiés pour `symbol` et filtre sur la fenêtre."""
    listing = http_get(f"{PUBLIC}/{CATEGORY}/{symbol}/").decode("utf-8", "replace")
    dates = sorted(set(re.findall(
        rf'href="{re.escape(symbol)}(\d{{4}}-\d{{2}}-\d{{2}})\.csv\.gz"', listing)))
    if not dates:
        raise SystemExit(f"Aucune archive trouvée pour {symbol} sur {PUBLIC}/{CATEGORY}/. "
                         f"Vérifie le symbole (dérivés seulement, ex. BTCUSDT).")
    d0, d1 = parse_day(start, last=False), parse_day(end, last=True)
    plan = []
    for d in dates:
        day = dt.date.fromisoformat(d)
        if d0 is not None and day < d0:
            continue
        if d1 is not None and day > d1:
            continue
        plan.append(Item(f"daily/{d}", f"{PUBLIC}/{CATEGORY}/{symbol}/{symbol}{d}.csv.gz", d))
    return plan


# --------------------------------------------------------------------------- #
# Parsing d'un csv.gz Bybit -> (ts_ms, price, size, side)
# Index FIXES : 0=timestamp (s epoch, fraction sous-ms), 2=side (Buy/Sell = agresseur),
# 3=size (actif de base), 4=price. Les colonnes suivantes (trdMatchID UUID, notionnels,
# RPI…) varient selon l'époque du fichier et sont ignorées. Entête sauté (champ 0 non
# numérique).
# --------------------------------------------------------------------------- #
def iter_rows(gz_path: str):
    with gzip.open(gz_path, "rt", encoding="utf-8", newline="") as f:
        for r in csv.reader(f):
            if not r:
                continue
            try:
                ts = round(float(r[0]) * 1000)
            except ValueError:
                continue  # ligne d'entête
            yield ts, float(r[4]), float(r[3]), "buy" if r[2] == "Buy" else "sell"


# --------------------------------------------------------------------------- #
# Écrivains : SQLite et CSV gzip
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
        # INDEX ts DIFFÉRÉ comme binance_history.py : construit en une fois à la fin
        # du plan complet (ensure_ts_index) -> l'ingestion reste un simple append.
        c.execute("""CREATE TABLE IF NOT EXISTS _ingested(
                        name TEXT PRIMARY KEY, rows INTEGER, at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS _meta(k TEXT PRIMARY KEY, v TEXT)""")
        if first:
            for k, v in (("symbol", symbol), ("market", "linear"),
                         ("exchange", "bybit"), ("source", "bybit-public-archives")):
                c.execute("INSERT OR REPLACE INTO _meta VALUES(?,?)", (k, v))
        c.commit()

    def done(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM _ingested")}

    def max_ts(self) -> int | None:
        r = self.conn.execute("SELECT max(ts) FROM trades").fetchone()
        return r[0] if r and r[0] is not None else None

    def ingest(self, item: Item, gz_path: str) -> tuple[int, int]:
        """UNE transaction par fichier (données + marqueur `_ingested`) : sans id de
        dédup, l'atomicité remplace l'INSERT OR IGNORE de Binance. Le flux est inséré
        tel quel s'il est chronologique (cas normal mesuré) ; à la première inversion,
        rollback et seconde passe TRIÉE (vieux fichiers à l'ordre non garanti)."""
        c = self.conn
        try:
            rows = self._insert_streaming(item, gz_path)
        except _OutOfOrder:
            c.rollback()
            print(f"    (flux non chronologique -> tri de {item.period})")
            rows = self._insert_sorted(item, gz_path)
        c.commit()  # data + marqueur de reprise = même transaction
        return rows, rows

    def _insert_streaming(self, item: Item, gz_path: str) -> int:
        c = self.conn
        cur = c.cursor()
        cur.execute("BEGIN")
        rows = 0
        prev = -1
        batch: list[tuple] = []
        for ts, price, size, side in iter_rows(gz_path):
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

    def _insert_sorted(self, item: Item, gz_path: str) -> int:
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        # tri STABLE sur ts seul : à ts égal, l'ordre du fichier (= ordre exchange) prime
        data = sorted(iter_rows(gz_path), key=lambda r: r[0])
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
    """Flux du fichier non chronologique : on repasse en mode trié."""


class CsvSink:
    """Un .csv.gz normalisé par jour. Reprise = présence du fichier final."""
    def __init__(self, out_dir: str, symbol: str) -> None:
        os.makedirs(out_dir, exist_ok=True)
        self.dir = out_dir
        self.symbol = symbol

    def _path(self, item: Item) -> str:
        return os.path.join(self.dir, f"{self.symbol}-bybit-trades-{item.period}.csv.gz")

    def done(self) -> set[str]:
        names: set[str] = set()
        for fn in os.listdir(self.dir) if os.path.isdir(self.dir) else []:
            if fn.endswith(".csv.gz"):
                tag = fn.removeprefix(f"{self.symbol}-bybit-trades-").removesuffix(".csv.gz")
                names.add(f"daily/{tag}")
        return names

    def max_ts(self) -> int | None:
        return None  # fichiers indépendants : pas de contrainte d'ordre global

    def ingest(self, item: Item, gz_path: str) -> tuple[int, int]:
        final = self._path(item)
        tmp = final + ".part"
        rows = 0
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
            w = csv.writer(gz)
            w.writerow(["ts", "price", "size", "side"])
            for ts, price, size, side in sorted(iter_rows(gz_path), key=lambda r: r[0]):
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
# Producteur (téléchargement + CRC gzip) -> file bornée -> consommateur (ingest)
# --------------------------------------------------------------------------- #
def producer(plan: list[Item], cache: str, q: "queue.Queue", stop: threading.Event) -> None:
    for it in plan:
        if stop.is_set():
            break
        dest = os.path.join(cache, it.url.rsplit("/", 1)[-1])
        try:
            if not (os.path.exists(dest) and verify_gzip(dest)):
                download_file(it.url, dest)
                if not verify_gzip(dest):
                    # un retéléchargement avant d'abandonner
                    download_file(it.url, dest)
                    if not verify_gzip(dest):
                        raise RuntimeError("gzip corrompu (CRC)")
            q.put((it, dest))
        except Exception as exc:  # noqa: BLE001
            q.put(("ERROR", f"{it.name}: {exc}"))
            return
    q.put((None, None))  # sentinelle de fin


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def run(args: argparse.Namespace) -> int:
    symbol = args.symbol.upper()
    print(f"== Bybit history — {symbol} [{CATEGORY} = dérivés] ==")
    print("Construction du plan (listing public.bybit.com)…")
    plan = build_plan(symbol, args.start, args.end)
    if not plan:
        print("Plan vide (aucune période dans la fenêtre).")
        return 0

    if args.format == "sqlite":
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-bybit-api.db")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        sink: SqliteSink | CsvSink = SqliteSink(out, symbol)
    else:
        out = args.out or os.path.join(DATA_DIR, f"{symbol}-bybit-trades")
        sink = CsvSink(out, symbol)

    done = sink.done()
    todo = [it for it in plan if it.name not in done]

    # Garde-fou chronologie (hypothèse candles.py : rowid = ordre chrono) : sans id de
    # trade, un backfill AVANT le contenu existant casserait l'ordre des rowids.
    if todo and (mx := sink.max_ts()) is not None:
        max_day = dt.datetime.fromtimestamp(mx / 1000, dt.timezone.utc).date()
        first_new = dt.date.fromisoformat(todo[0].period)
        if first_new <= max_day:
            print(f"REFUS : la base contient déjà des trades jusqu'au {max_day} ; "
                  f"ingérer {first_new} casserait l'ordre chronologique des rowids.\n"
                  f"-> pour du backfill, reconstruire la base sur la fenêtre complète "
                  f"(ou utiliser --out vers une nouvelle base).")
            sink.close()
            return 1

    span = f"{plan[0].period} … {plan[-1].period}"
    print(f"Plan : {len(plan)} fichiers ({span}) ; déjà fait : "
          f"{len(done & {p.name for p in plan})} ; à traiter : {len(todo)}")
    print(f"Sortie : {out}  (format={args.format})")

    if args.dry_run:
        for it in todo[:10]:
            print("  -", it.name, "->", it.url)
        if len(todo) > 10:
            print(f"  … (+{len(todo) - 10} autres)")
        print("DRY-RUN : rien téléchargé.")
        sink.close()
        return 0

    def finalize_if_complete() -> None:
        if [it for it in plan if it.name not in sink.done()]:
            print("Plan non terminé -> l'index ts sera construit au run qui le complète.")
            return
        if not sink.has_ts_index():
            print("Plan complet -> construction de l'index ts (différé)…")
            t = time.monotonic()
            sink.ensure_ts_index()
            print(f"  index ts OK ({time.monotonic() - t:.0f}s)")

    if not todo:
        print("Tout est déjà à jour.")
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
            if it == "ERROR":                    # échec producteur -> stop (reprenable)
                print(f"\n!! Échec téléchargement {dest}. Relance la commande pour reprendre.")
                break
            i += 1
            r, n = sink.ingest(it, dest)
            total_rows += r
            total_new += n
            if not args.keep_gz:
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
    print(f"Terminé : +{human(total_new)} trades en {time.monotonic() - t0:.0f}s.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extracteur autonome d'historique de trades Bybit (public.bybit.com).")
    p.add_argument("--symbol", default="BTCUSDT", help="défaut BTCUSDT (dérivés)")
    p.add_argument("--start", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (défaut: début dispo)")
    p.add_argument("--end", default=None, help="YYYY | YYYY-MM | YYYY-MM-DD (défaut: dernier publié)")
    p.add_argument("--format", default="sqlite", choices=["sqlite", "csv"])
    p.add_argument("--out", default=None,
                   help="fichier .db (sqlite) ou dossier (csv) ; défaut : data\\<SYMBOLE>-bybit-api.db")
    p.add_argument("--cache", default=os.path.join(DATA_DIR, "_dumps"),
                   help="cache des csv.gz téléchargés (défaut : data\\_dumps)")
    p.add_argument("--prefetch", type=int, default=2, help="fichiers téléchargés en avance")
    p.add_argument("--keep-gz", action="store_true", help="garder les csv.gz bruts après ingestion")
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
