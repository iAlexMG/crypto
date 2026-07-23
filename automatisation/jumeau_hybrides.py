# jumeau_hybrides.py — BACKTEST des 3 hybrides (H1/H2/H3) sur l'historique Binance 1 m d'un jour,
# via le moteur PARTAGÉ hybrides.py (identique aux jumeaux des indices). Écrit un journal de
# décisions par stratégie (matière de la parité) + un BILAN (entrées, sorties par type, modifs).
#
# Usage :
#   python jumeau_hybrides.py --date 2026-07-23                 # les 3
#   python jumeau_hybrides.py --date 2026-07-23 --strategie h2  # une seule
import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from hybrides import MoteurHybride, CONFIGS

BINANCE = "https://fapi.binance.com/fapi/v1/klines"
SYMBOL = "BTCUSDT"
JUMEAU_BASE = Path(__file__).resolve().parent / "journaux" / "_jumeau"


def klines_jour(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = int(d.timestamp() * 1000)
    url = f"{BINANCE}?" + urllib.parse.urlencode(
        {"symbol": SYMBOL, "interval": "1m", "startTime": start,
         "endTime": start + 24 * 3600 * 1000, "limit": 1500})
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def rejouer(strat, date_str, rows):
    cfg = CONFIGS[strat]
    d = JUMEAU_BASE / cfg["slug"]
    d.mkdir(parents=True, exist_ok=True)
    fichier = d / f"{date_str}.ndjson"
    stats = {"entree": 0, "SL": 0, "TP": 0, "SIGNAL": 0, "stop_modifie": 0}
    f = open(fichier, "w", encoding="utf-8")

    def emit(evenement, ts, **champs):
        if evenement == "entree":
            stats["entree"] += 1
        elif evenement == "stop_modifie":
            stats["stop_modifie"] += 1
        elif evenement == "sortie":
            stats[champs.get("code", "?")] = stats.get(champs.get("code", "?"), 0) + 1
        t = datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()
        rec = {"ts": t, "strategie": cfg["slug"], "symbole": SYMBOL, "mode": "jumeau",
               "evenement": evenement, **champs}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    moteur = MoteurHybride(strat, emit=emit)
    for k in rows:
        moteur.barre(int(k[0]) + 60_000, float(k[1]), float(k[2]), float(k[3]), float(k[4]))
    f.close()
    print(f"  {strat} {cfg['nom']:16} : {stats['entree']} entrées | "
          f"{stats['SL']} SL, {stats['TP']} TP, {stats['SIGNAL']} croix inverse | "
          f"stop modifié {stats['stop_modifie']}× -> {fichier.name}")


def main():
    ap = argparse.ArgumentParser(description="Backtest des 3 hybrides (historique Binance).")
    ap.add_argument("--date", required=True, help="jour UTC (YYYY-MM-DD)")
    ap.add_argument("--strategie", choices=["h1", "h2", "h3", "tous"], default="tous")
    a = ap.parse_args()
    rows = klines_jour(a.date)
    strats = ["h1", "h2", "h3"] if a.strategie == "tous" else [a.strategie]
    print(f"=== BACKTEST hybrides — {a.date} ({len(rows)} bougies 1 m Binance) ===")
    for s in strats:
        rejouer(s, a.date, rows)


if __name__ == "__main__":
    main()
