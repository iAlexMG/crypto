# parite_sma.py — PARITÉ : décisions du runner SHADOW (live) vs JUMEAU backtest du même jour.
# Compare les signaux d'entrée (minute + sens) DANS les fenêtres où le shadow tournait
# (demarrage -> arret). Pendant crypto de indices/.../parite_shadow.py.
#
# Usage :
#   python parite_sma.py --date 2026-07-23
import argparse
import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent / "journaux"
SHADOW_DIR = BASE / "sma_bitget"
JUMEAU_DIR = BASE / "_jumeau" / "sma_bitget"


def charger(fichier):
    evs = [json.loads(l) for l in open(fichier, encoding="utf-8") if l.strip()]
    evs.sort(key=lambda e: e["ts"])
    return evs


def minute(ts):
    return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")


def signaux(evs):
    """{(minute, sens)} des signaux d'entrée."""
    return {(minute(e["ts"]), e.get("sens", "?")): e.get("raison", "")
            for e in evs if e["evenement"] == "signal"}


def fenetres(evs):
    f, deb = [], None
    for e in evs:
        if e["evenement"] == "demarrage":
            deb = e["ts"]
        elif e["evenement"] == "arret" and deb:
            f.append((deb, e["ts"]))
            deb = None
    if deb:
        f.append((deb, "9999"))
    return f


def dans(ts, f):
    return any(a <= ts <= b for a, b in f)


def comparer(date):
    fs = SHADOW_DIR / f"{date}.ndjson"
    fj = JUMEAU_DIR / f"{date}.ndjson"
    if not fs.exists():
        print(f"⛔ journal shadow absent : {fs}")
        return
    if not fj.exists():
        print(f"⛔ jumeau absent : {fj}  (lance d'abord : python jumeau_sma.py --date {date})")
        return

    evs_s = charger(fs)
    fen = fenetres(evs_s)
    sig_s = signaux(evs_s)
    # jumeau : ne garder que les signaux DANS les fenêtres où le shadow tournait.
    sig_j = {k: v for k, v in signaux(charger(fj)).items()
             if dans(datetime.strptime(k[0], "%Y-%m-%d %H:%M").isoformat(), fen)}

    communs = sig_s.keys() & sig_j.keys()
    shadow_seul = sig_s.keys() - sig_j.keys()
    jumeau_seul = sig_j.keys() - sig_s.keys()
    total = len(sig_s.keys() | sig_j.keys())
    print(f"=== PARITÉ SMA — {date} ===")
    print(f"Fenêtres shadow : {len(fen)} | signaux shadow : {len(sig_s)} | "
          f"jumeau (dans fenêtres) : {len(sig_j)}")
    print(f"Concordants : {len(communs)} | shadow seul : {len(shadow_seul)} | "
          f"jumeau seul : {len(jumeau_seul)}")
    if total:
        print(f"Parité décisions : {100 * len(communs) / total:.1f}%")
    for m, s in sorted(shadow_seul):
        print(f"  SHADOW SEUL  {m} {s}")
    for m, s in sorted(jumeau_seul):
        print(f"  JUMEAU SEUL  {m} {s}")
    if communs and not shadow_seul and not jumeau_seul:
        print("✅ PARITÉ PARFAITE — mêmes signaux, mêmes minutes, mêmes sens.")


def main():
    ap = argparse.ArgumentParser(description="Parité shadow ↔ jumeau (SMA cross Bitget).")
    ap.add_argument("--date", required=True, help="jour UTC (YYYY-MM-DD)")
    comparer(ap.parse_args().date)


if __name__ == "__main__":
    main()
