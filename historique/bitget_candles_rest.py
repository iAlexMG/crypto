#!/usr/bin/env python3
r"""Voie B Bitget : chandelles 1 m via l'API REST DE MARCHE (api.bitget.com).

DEUX SOURCES REELLEMENT DIFFERENTES — le point de la comparaison A/B :
  - la voie A (`bitget_history.py`) vient du PORTAIL D'ARCHIVES
    (bitget.com/data-download -> img.bitgetimg.com) : un pipeline batch qui
    genere des fichiers publies avec ~1 jour de retard ;
  - la voie B (ce script) vient de l'API DE MARCHE temps reel
    (`GET api.bitget.com/api/v2/mix/market/history-candles`) : l'infrastructure
    de trading elle-meme — exactement ce que Quantower aurait relaye si Bitget
    figurait dans ses connexions (le constat OKX/KuCoin : la voie B « Quantower »
    n'a jamais ete que le relais des chandelles servies par l'exchange).
  NB il existe aussi des archives de chandelles sur le portail
  (`bitget_klines.py`) — MEME source que la voie A, donc verif interne
  seulement, pas une voie B.

ENDPOINT (mesure) :
  GET /api/v2/mix/market/history-candles?symbol=BTCUSDT&productType=usdt-futures
      &granularity=1m&endTime=<ms>&limit=200
  - rend jusqu'a 200 chandelles ASCENDANTES strictement AVANT endTime (exclusif :
    endTime = minuit -> derniere barre 23:59) ;
  - champs [ts ms, open, high, low, close, baseVolume, quoteVolume] — volume
    deja en actif de base, pas de multiplier ;
  - profondeur verifiee : >= 41 jours en 1 m (doc : « all historical K-line
    data », plage startTime/endTime max 90 j) ; limite 20 req/s par IP.

SORTIE : SQLite `data\<SYMBOLE>-bitget-perp-rest1m.db`, table `bars` (schema
  compare_ab.py, cote B « chandelles deja faites »). ts = cle primaire +
  INSERT OR REPLACE : idempotent et relançable ; marqueurs `_ingested` par
  tranche de 200 minutes pour la reprise.

EXEMPLES :
  python bitget_candles_rest.py --start 2026-06-01 --end 2026-07-11 --dry-run
  python bitget_candles_rest.py --start 2026-06-01 --end 2026-07-11
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.request

REST = ("https://api.bitget.com/api/v2/mix/market/history-candles"
        "?symbol={sym}&productType=usdt-futures&granularity=1m"
        "&endTime={end}&limit=200")
UA = {"User-Agent": "Mozilla/5.0 (bitget-candles-extractor)"}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CHUNK_MS = 200 * 60_000  # 200 barres de 1 m par requete


def http_json(url: str, retries: int = 6, timeout: int = 30) -> dict:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2 ** i, 30))
    raise last  # type: ignore[misc]


def parse_day(s: str, last: bool) -> dt.date:
    parts = [int(x) for x in s.split("-")]
    y = parts[0]
    m = parts[1] if len(parts) > 1 else (12 if last else 1)
    if len(parts) > 2:
        return dt.date(y, m, parts[2])
    if not last:
        return dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def open_db(path: str, symbol: str) -> sqlite3.Connection:
    first = not os.path.exists(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS bars(
                       ts           INTEGER PRIMARY KEY,
                       open  REAL NOT NULL, high REAL NOT NULL,
                       low   REAL NOT NULL, close REAL NOT NULL,
                       volume       REAL NOT NULL,
                       quote_volume REAL NOT NULL)""")
    conn.execute("CREATE TABLE IF NOT EXISTS _ingested(name TEXT PRIMARY KEY, "
                 "rows INTEGER, at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS _meta(k TEXT PRIMARY KEY, v TEXT)")
    if first:
        for k, v in (("symbol", symbol), ("market", "swap"), ("exchange", "bitget"),
                     ("contract_val", "1"), ("source", "bitget-rest-history-candles"),
                     ("granularity", "1m")):
            conn.execute("INSERT OR REPLACE INTO _meta VALUES(?,?)", (k, v))
    conn.commit()
    return conn


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Chandelles 1 m Bitget Futures via l'API REST de marche "
                    "(history-candles) -> base `bars` pour compare_ab.py.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start", required=True, help="YYYY | YYYY-MM | YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY | YYYY-MM | YYYY-MM-DD")
    p.add_argument("--out", default=None,
                   help="defaut : data\\<SYMBOLE>-bitget-perp-rest1m.db")
    p.add_argument("--pause", type=float, default=0.15,
                   help="pause entre requetes en s (limite 20 req/s IP)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    symbol = args.symbol.upper()
    d0 = parse_day(args.start, last=False)
    d1 = parse_day(args.end, last=True) + dt.timedelta(days=1)
    t0 = int(dt.datetime(d0.year, d0.month, d0.day,
                         tzinfo=dt.timezone.utc).timestamp() * 1000)
    t1 = int(dt.datetime(d1.year, d1.month, d1.day,
                         tzinfo=dt.timezone.utc).timestamp() * 1000)
    chunks = [(lo, min(lo + CHUNK_MS, t1)) for lo in range(t0, t1, CHUNK_MS)]

    print(f"== Bitget REST history-candles 1 m — {symbol} ==")
    print(f"Fenetre UTC : {d0} 00:00 -> {d1} 00:00 "
          f"({human((t1 - t0) // 60000)} minutes, {len(chunks)} requetes)")

    out = args.out or os.path.join(DATA_DIR, f"{symbol}-bitget-perp-rest1m.db")
    conn = open_db(out, symbol)
    done = {r[0] for r in conn.execute("SELECT name FROM _ingested")}
    todo = [(lo, hi) for lo, hi in chunks if f"chunk/{lo}" not in done]
    print(f"Deja fait : {len(chunks) - len(todo)} ; a traiter : {len(todo)}")
    print(f"Sortie : {out}")
    if args.dry_run:
        print("DRY-RUN : rien telecharge.")
        conn.close()
        return 0

    t_run = time.monotonic()
    total = 0
    empty_streak = 0
    for i, (lo, hi) in enumerate(todo, 1):
        d = http_json(REST.format(sym=symbol, end=hi))
        if str(d.get("code")) != "00000":
            print(f"ERREUR API sur endTime={hi} : {d}", file=sys.stderr)
            conn.close()
            return 1
        rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                 float(r[5]), float(r[6]))
                for r in (d.get("data") or []) if lo <= int(r[0]) < hi]
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?)", rows)
        cur.execute("INSERT OR REPLACE INTO _ingested VALUES(?,?,?)",
                    (f"chunk/{lo}", len(rows),
                     dt.datetime.now(dt.timezone.utc).isoformat()))
        conn.commit()
        total += len(rows)
        empty_streak = empty_streak + 1 if not rows else 0
        if empty_streak >= 5:
            print("5 tranches vides consecutives : hors de la profondeur servie ? "
                  "Arret prudent (relançable).", file=sys.stderr)
            conn.close()
            return 1
        if i % 25 == 0 or i == len(todo):
            el = time.monotonic() - t_run
            print(f"[{i:>4}/{len(todo)}] cumul {human(total)} barres | {el:5.0f}s",
                  flush=True)
        time.sleep(args.pause)

    lo_ = conn.execute("SELECT min(ts), max(ts), count(*) FROM bars").fetchone()
    if lo_ and lo_[2]:
        def iso(ms): return dt.datetime.fromtimestamp(
            ms / 1000, dt.timezone.utc).isoformat()
        print(f"Base : {human(lo_[2])} barres, {iso(lo_[0])} → {iso(lo_[1])}")
    conn.close()
    print(f"Termine en {time.monotonic() - t_run:.0f}s.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrompu — etat sauvegarde (reprenable en relancant).")
        raise SystemExit(130)
