# jumeau_sma.py — JUMEAU BACKTEST de la stratégie du runner (SMA 9/21 cross 1 m), rejoué sur
# l'HISTORIQUE Binance d'un jour. Écrit un journal de décisions au MÊME format que le runner
# shadow → matière de la PARITÉ (« en démo, décide-t-il comme au backtest ? », cf. phase 4
# indices). Même logique que runner_sma (SMA/ATR/croisement/cooldown) → mêmes signaux sur les
# mêmes bougies. Mode SHADOW du runner = cooldown-gated (pas de position simulée) : le jumeau
# fait pareil.
#
# ⏱ L'horodatage du signal = FRONTIÈRE DE CLÔTURE de la bougie déclencheuse (openTime + 60 s) =
#    l'instant où le runner live la détecte (juste après la clôture). Aligne les minutes.
#
# Usage :
#   python jumeau_sma.py --date 2026-07-23
import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from runner_sma import (SMA_RAPIDE, SMA_LENTE, ATR_N, COOLDOWN_S,
                        SYMBOL_BINANCE, SYMBOL_BITGET, sma, atr)

BINANCE = "https://fapi.binance.com/fapi/v1/klines"
JUMEAU_DIR = Path(__file__).resolve().parent / "journaux" / "_jumeau" / "sma_bitget"


def klines_jour(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start = int(d.timestamp() * 1000)
    end = start + 24 * 3600 * 1000
    url = f"{BINANCE}?" + urllib.parse.urlencode(
        {"symbol": SYMBOL_BINANCE, "interval": "1m", "startTime": start,
         "endTime": end, "limit": 1500})
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def rejouer(date_str):
    rows = klines_jour(date_str)
    JUMEAU_DIR.mkdir(parents=True, exist_ok=True)
    fichier = JUMEAU_DIR / f"{date_str}.ndjson"
    closes, highs, lows = [], [], []
    diff_prec = None
    cooldown_ms = 0
    n_sig = 0
    with open(fichier, "w", encoding="utf-8") as f:
        for k in rows:
            open_ms = int(k[0])
            closes.append(float(k[4])); highs.append(float(k[2])); lows.append(float(k[3]))
            if len(closes) < SMA_LENTE + 1:
                continue
            sr, sl = sma(closes, SMA_RAPIDE), sma(closes, SMA_LENTE)
            a = atr(highs, lows, closes, ATR_N)
            diff = sr - sl
            crois = 0
            if diff_prec is not None:
                if diff_prec <= 0 < diff:
                    crois = 1
                elif diff_prec >= 0 > diff:
                    crois = -1
            diff_prec = diff
            sig_ms = open_ms + 60_000                      # frontière de clôture
            if crois and a and sig_ms >= cooldown_ms:
                sens = "long" if crois > 0 else "short"
                t = datetime.fromtimestamp(sig_ms / 1000, timezone.utc)
                rec = {"ts": t.isoformat(), "strategie": "sma_bitget", "symbole": SYMBOL_BITGET,
                       "mode": "jumeau", "evenement": "signal", "prix": round(closes[-1], 1),
                       "sens": sens,
                       "raison": f"croisement {'haussier' if crois > 0 else 'baissier'} -> {sens}",
                       "sma_rapide": round(sr, 1), "sma_lente": round(sl, 1), "atr": round(a, 1)}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_sig += 1
                cooldown_ms = sig_ms + COOLDOWN_S * 1000
    print(f"✅ jumeau {date_str} : {len(rows)} bougies, {n_sig} signaux -> {fichier}")


def main():
    ap = argparse.ArgumentParser(description="Jumeau backtest SMA cross (historique Binance).")
    ap.add_argument("--date", required=True, help="jour UTC (YYYY-MM-DD)")
    rejouer(ap.parse_args().date)


if __name__ == "__main__":
    main()
