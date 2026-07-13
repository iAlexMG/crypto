#!/usr/bin/env python3
r"""Voie B Bitget : chandelles 1 m OFFICIELLES depuis les archives publiques.

Pendant de la voie B Quantower des autres venues — mais Quantower ne supporte PAS
Bitget (liste officielle des connexions : Binance/Bybit/OKX/Kraken/KuCoin/…, pas
Bitget). Or le constat OKX/KuCoin etait que la voie B « graphique » n'est que le
relais des CHANDELLES servies par l'exchange : ici on va les chercher directement
a la source officielle, sans intermediaire. Meme portail que bitget_history.py
(bitget.com/data-download), businessType=1 (chandelles) :

  POST https://www.bitget.com/v1/statistics/public/download/getPublicDataV2
       {displaySymbol:["BTCUSDT"], businessLine:2, businessType:1, dateType:1,
        beginTimeStr, endTimeStr}   (plage max 7 jours par appel)
  -> UN zip par jour : https://img.bitgetimg.com/online/kline/<SYM>/UMCBL/<AAAAMMJJ>.zip
     contenant UN xlsx de 1440 lignes : timestamp (SECONDES epoch), open, high,
     low, close, basevolume (actif de base), usdtvolume (quote).

PARTICULARITES (mesurees sur le temoin 20260701) :
  - >> PIEGE UTC+8 (identique aux trades) : le fichier « JJ » couvre
     [JJ-1 16:00, JJ 16:00) UTC — 1440 minutes de la journee UTC+8. Couvrir la
     fenetre UTC [start, end 24:00) demande les fichiers start .. end+1 et un
     filtre de lignes a l'ingestion ;
  - le xlsx se parse en stdlib (zipfile + ElementTree) — pas de dependance
    openpyxl. Cellules mesurees : t="inlineStr" (<is><t>…</t></is>) pour l'entete
    ET les prix/volumes, t="n" pour le timestamp ; sharedStrings gere en repli ;
  - basevolume est DEJA en actif de base (BTC) : pas de multiplier ;
  - ts stocke en MILLISECONDES (x1000) pour rester au schema commun du projet.

SORTIE : SQLite `data\<SYMBOLE>-bitget-perp-kline.db`, table `bars(ts PK ms,
  open, high, low, close, volume, quote_volume)` — le schema que compare_ab.py
  detecte automatiquement (cote B « chandelles deja faites », cas KuCoin/OKX).
  ts = cle primaire + INSERT OR REPLACE : idempotent, relançable sans garde-fou
  d'ordre (pas d'hypothese rowid ici).

EXEMPLES :
  python bitget_klines.py --start 2026-06-01 --end 2026-07-11 --dry-run
  python bitget_klines.py --start 2026-06-01 --end 2026-07-11
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

LISTING = "https://www.bitget.com/v1/statistics/public/download/getPublicDataV2"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HEADER = ["timestamp", "open", "high", "low", "close", "basevolume", "usdtvolume"]


def http_get(url: str, retries: int = 5, timeout: int = 120) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(2 ** i, 30))
    raise last  # type: ignore[misc]


def http_post_json(url: str, body: dict, retries: int = 6, timeout: int = 60) -> dict:
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


def build_plan(symbol: str, d0: dt.date, d1_arch: dt.date) -> list[tuple[str, str]]:
    """[(jour ISO, url)] tries — listing par tranches de 7 jours (sa plage max)."""
    plan: list[tuple[str, str]] = []
    day = d0
    while day <= d1_arch:
        hi = min(day + dt.timedelta(days=6), d1_arch)
        resp = http_post_json(LISTING, {
            "displaySymbol": [symbol], "businessLine": 2, "businessType": 1,
            "dateType": 1, "beginTimeStr": day.isoformat(), "endTimeStr": hi.isoformat()})
        if str(resp.get("code")) != "200":
            raise SystemExit(f"Listing en echec sur {day}..{hi} : {resp}")
        for f in resp.get("data") or []:
            if f.get("displayName") == symbol:
                plan.append((f["dateTimeStr"], f["fileUrl"]))
        day = hi + dt.timedelta(days=1)
        if day <= d1_arch:
            time.sleep(1.2)
        plan.sort()
    want = {(d0 + dt.timedelta(days=i)).isoformat()
            for i in range((d1_arch - d0).days + 1)}
    missing = sorted(want - {p[0] for p in plan})
    if missing:
        print(f"ATTENTION : {len(missing)} jour(s) absents du listing "
              f"(publication ~1 jour de retard) : {', '.join(missing[:5])}"
              f"{' …' if len(missing) > 5 else ''}")
    return plan


# --------------------------------------------------------------------------- #
# xlsx -> lignes, en stdlib (les fichiers font 1440 lignes : ElementTree suffit)
# --------------------------------------------------------------------------- #
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def xlsx_rows(xlsx_bytes: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        sheet = next(n for n in z.namelist()
                     if re.match(r"xl/worksheets/sheet1\.xml$", n))
        root = ET.fromstring(z.read(sheet))
        rows: list[list[str]] = []
        for row in root.iter(f"{NS}row"):
            vals: list[str] = []
            for c in row.iter(f"{NS}c"):
                t = c.get("t")
                if t == "inlineStr":  # <is><t>…</t></is> (cas Bitget mesure)
                    vals.append("".join(e.text or "" for e in c.iter(f"{NS}t")))
                    continue
                v = c.find(f"{NS}v")
                if v is None or v.text is None:
                    vals.append("")
                elif t == "s":
                    vals.append(shared[int(v.text)])
                else:
                    vals.append(v.text)
            rows.append(vals)
        return rows


def iter_bars(zip_path: str, t0: int, t1: int):
    """(ts_ms, o, h, l, c, volume_base, volume_quote) filtre sur [t0, t1)."""
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        rows = xlsx_rows(z.read(name))
    if rows and rows[0] and rows[0][0] != HEADER[0]:
        raise SystemExit(f"Entete inattendue dans {name} : {rows[0]} "
                         f"(attendu {HEADER}) — format change, verifier.")
    for r in rows[1:]:
        if len(r) < 7 or not r[0]:
            continue
        ts = int(float(r[0])) * 1000  # secondes -> ms
        if ts < t0 or ts >= t1:
            continue
        yield (ts, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
               float(r[5]), float(r[6]))


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
                     ("contract_val", "1"), ("source", "bitget-public-kline-archives"),
                     ("granularity", "1m")):
            conn.execute("INSERT OR REPLACE INTO _meta VALUES(?,?)", (k, v))
    conn.commit()
    return conn


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Chandelles 1 m officielles Bitget Futures (archives publiques) "
                    "-> base `bars` pour compare_ab.py.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start", required=True, help="YYYY | YYYY-MM | YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY | YYYY-MM | YYYY-MM-DD")
    p.add_argument("--out", default=None,
                   help="defaut : data\\<SYMBOLE>-bitget-perp-kline.db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    symbol = args.symbol.upper()
    d0 = parse_day(args.start, last=False)
    d1 = parse_day(args.end, last=True)
    d1x = d1 + dt.timedelta(days=1)
    t0 = int(dt.datetime(d0.year, d0.month, d0.day,
                         tzinfo=dt.timezone.utc).timestamp() * 1000)
    t1 = int(dt.datetime(d1x.year, d1x.month, d1x.day,
                         tzinfo=dt.timezone.utc).timestamp() * 1000)

    print(f"== Bitget klines 1 m — {symbol} ==")
    print(f"Fenetre UTC : {d0} 00:00 -> {d1x} 00:00 "
          f"(archives {d0} .. {d1x}, piege UTC+8)")
    plan = build_plan(symbol, d0, d1x)
    if not plan:
        print("Plan vide.")
        return 0

    out = args.out or os.path.join(DATA_DIR, f"{symbol}-bitget-perp-kline.db")
    conn = open_db(out, symbol)
    done = {r[0] for r in conn.execute("SELECT name FROM _ingested")}
    todo = [(day, url) for day, url in plan if f"daily/{day}" not in done]
    print(f"Plan : {len(plan)} jours ({plan[0][0]} … {plan[-1][0]}) ; deja fait : "
          f"{len(plan) - len(todo)} ; a traiter : {len(todo)}")
    print(f"Sortie : {out}")
    if args.dry_run:
        for day, url in todo[:5]:
            print("  -", day, "->", url)
        if len(todo) > 5:
            print(f"  … (+{len(todo) - 5} autres)")
        print("DRY-RUN : rien telecharge.")
        conn.close()
        return 0

    t_run = time.monotonic()
    total = 0
    for i, (day, url) in enumerate(todo, 1):
        blob = http_get(url)
        tmp = os.path.join(DATA_DIR, "_dumps", f"kline_{day}.zip")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(blob)
        rows = list(iter_bars(tmp, t0, t1))
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?)", rows)
        cur.execute("INSERT OR REPLACE INTO _ingested VALUES(?,?,?)",
                    (f"daily/{day}", len(rows),
                     dt.datetime.now(dt.timezone.utc).isoformat()))
        conn.commit()
        os.remove(tmp)
        total += len(rows)
        print(f"[{i:>3}/{len(todo)}] {day}  {human(len(rows)):>6} barres "
              f"| cumul {human(total)}", flush=True)

    lo = conn.execute("SELECT min(ts), max(ts), count(*) FROM bars").fetchone()
    if lo and lo[2]:
        def iso(ms): return dt.datetime.fromtimestamp(
            ms / 1000, dt.timezone.utc).isoformat()
        print(f"Base : {human(lo[2])} barres, {iso(lo[0])} → {iso(lo[1])}")
    conn.close()
    print(f"Termine en {time.monotonic() - t_run:.0f}s.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrompu — etat sauvegarde (reprenable en relancant).")
        raise SystemExit(130)
