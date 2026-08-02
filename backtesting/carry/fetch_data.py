#!/usr/bin/env python3
"""Récupère les données du carry funding/basis BTCUSDT (Binance) — stdlib seule.

Trois séries, toutes publiques et gratuites, alignées sur la grille de funding (8h,
00/08/16 UTC) :
  data/funding.csv          funding rate history (perp)          -> /fapi/v1/fundingRate
  data/klines_spot_8h.csv   klines 8h SPOT (open/high/low/close)  -> /api/v3/klines
  data/klines_perp_8h.csv   klines 8h PERP                        -> /fapi/v1/klines

Le funding est minuscule (~3 valeurs/jour) et immuable une fois passé : re-fetcher
est trivial. Même esprit autonome que ../../historique/binance_history.py (urllib,
csv, stdlib pure — copiable tel quel).

USAGE :
    python fetch_data.py                    # BTCUSDT, depuis 2019-09 (lancement du perp)
    python fetch_data.py --symbol ETHUSDT   # un autre actif
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (carry-fetch)"}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"
KLINES = {
    "spot": "https://api.binance.com/api/v3/klines",
    "perp": "https://fapi.binance.com/fapi/v1/klines",
}


def http_get(url: str, retries: int = 5, timeout: int = 30) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 — réseau, on réessaie
            last = exc
            time.sleep(min(2 ** i, 15))
    raise last  # type: ignore[misc]


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def fetch_funding(symbol: str, start_ms: int) -> list[tuple[int, float, float]]:
    """(fundingTime ms, fundingRate, markPrice) triés croissant."""
    rows, seen = [], set()
    start, now = start_ms, int(time.time() * 1000)
    while True:
        url = FUNDING + "?" + urllib.parse.urlencode(
            {"symbol": symbol, "startTime": start, "endTime": now, "limit": 1000})
        page = json.loads(http_get(url).decode())
        if not page:
            break
        for d in page:
            t = int(d["fundingTime"])
            if t in seen:
                continue
            seen.add(t)
            mp = float(d["markPrice"]) if d.get("markPrice") not in (None, "", "0") else float("nan")
            rows.append((t, float(d["fundingRate"]), mp))
        last_t = int(page[-1]["fundingTime"])
        if len(page) < 1000 or last_t >= now:
            break
        start = last_t + 1
        time.sleep(0.15)
    rows.sort()
    return rows


def fetch_klines(base: str, symbol: str, interval: str, start_ms: int) -> list[tuple]:
    """(openTime, open, high, low, close, volume) triés croissant."""
    rows, start, now = [], start_ms, int(time.time() * 1000)
    while True:
        url = base + "?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": start, "endTime": now, "limit": 1000})
        page = json.loads(http_get(url).decode())
        if not page:
            break
        for k in page:
            rows.append((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])))
        if len(page) < 1000:
            break
        start = int(page[-1][0]) + 1
        time.sleep(0.15)
    return rows


def save(path: str, header: list[str], rows) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch funding + klines 8h pour le carry.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="8h", help="cadence des klines (défaut 8h = grille funding)")
    p.add_argument("--start", default="2019-09-01", help="YYYY-MM-DD (défaut : lancement du perp BTC)")
    args = p.parse_args()
    start_ms = int(dt.datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

    print(f"Funding {args.symbol}…")
    fund = fetch_funding(args.symbol, start_ms)
    save(os.path.join(DATA_DIR, "funding.csv"), ["fundingTime", "iso", "fundingRate", "markPrice"],
         [(t, iso(t), f"{r:.8f}", f"{mp:.2f}") for t, r, mp in fund])
    print(f"  {len(fund)} points  {iso(fund[0][0])} -> {iso(fund[-1][0])}")

    for tag, base in KLINES.items():
        print(f"Klines {tag} {args.interval}…")
        kl = fetch_klines(base, args.symbol, args.interval, start_ms)
        save(os.path.join(DATA_DIR, f"klines_{tag}_{args.interval}.csv"),
             ["openTime", "iso", "open", "high", "low", "close", "volume"],
             [(t, iso(t), o, h, l, c, v) for t, o, h, l, c, v in kl])
        print(f"  {len(kl)} barres  {iso(kl[0][0])} -> {iso(kl[-1][0])}")
    print(f"-> {DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
