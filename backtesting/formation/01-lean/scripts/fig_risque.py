"""Figure : le MÊME signal RSI, sans et avec gestion du risque (stop/take).

  Haut — PRIX + les trades de la version RISQUE (achats ▲, ventes ▼) ; le trade
         catastrophe du RSI nu est annoté : coupé au stop à -9 % au lieu de -27 %.
  Bas  — deux ÉQUITÉS superposées : RSI nu (pointillé) vs RSI+risque.
         Le stop protège sur le krach initial, mais les re-entrées/re-stops rognent ensuite.

    python formation/01-lean/scripts/fig_risque.py
"""
import csv
import json
from datetime import datetime, timezone, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
BASE = "backtests/lean/Launcher/bin/Release/"
NAIF = BASE + "RsiRetourMoyenne.json"
RISQUE = BASE + "RisqueStops.json"
ORDRES = BASE + "RisqueStops-order-events.json"
OUT = "formation/01-lean/assets/images/lecon-06-risque.png"
STOP = 0.08
ENCRE, BLEU, VERT, ROUGE, GRIS = "#263238", "#1d4ed8", "#2e7d32", "#c62828", "#b0bec5"


def equity(path):
    d = json.load(open(path))
    v = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
    return [datetime.fromtimestamp(x[0], tz=timezone.utc) for x in v], [x[4] for x in v]


t6, e6 = equity(NAIF)
t9, e9 = equity(RISQUE)

# Prix (CSV source de vérité) pour situer les trades — tracé à la CLÔTURE de la barre
tp, close = [], []
with open(CSV) as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        tp.append(datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
                  .replace(tzinfo=timezone.utc) + timedelta(hours=1))
        close.append(float(row[4]))

fills = [e for e in json.load(open(ORDRES)) if e["status"] == "filled"]
def dt(e): return datetime.fromtimestamp(e["time"], tz=timezone.utc)
achats = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "buy"]
ventes = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "sell"]
positions = []
for k, a in enumerate(achats):
    fin_p = ventes[k][0] if k < len(ventes) else t9[-1]
    positions.append((a[0], fin_p))

fig, (axP, axE) = plt.subplots(2, 1, figsize=(12, 8.4), sharex=True,
                               gridspec_kw={"height_ratios": [1.4, 1]})
fig.subplots_adjust(left=0.08, right=0.975, top=0.93, bottom=0.075, hspace=0.13)
en_k = FuncFormatter(lambda v, _: f"{v/1000:.0f}k")

for i, (deb, fin_p) in enumerate(positions):
    axP.axvspan(deb, fin_p, color=VERT, alpha=0.10)

# Panneau prix : le prix horaire en fond + les trades à leur prix d'exécution réel
axP.plot(tp, close, color=GRIS, lw=0.8, alpha=0.9, label="Prix BTCUSDT (close 1 h)")
achx = [a[0] for a in achats]; achy = [a[1] for a in achats]
venx = [v[0] for v in ventes]; veny = [v[1] for v in ventes]
axP.plot(achx, achy, "^", ms=8, mfc=VERT, mec="white", mew=0.8, zorder=6, label=f"achat ▲ ×{len(achats)}")
axP.plot(venx, veny, "v", ms=8, mfc=ROUGE, mec="white", mew=0.8, zorder=6, label=f"vente ▼ ×{len(ventes)}")
# Annoter le trade catastrophe : TRADE 3 du journal = 3e ORDRE = 2e ACHAT (achats[1]),
# coupé par le stop = 2e VENTE (ventes[1]).
if len(achats) >= 2 and len(ventes) >= 2:
    axP.annotate(f"achat {achats[1][1]:,.0f} (RSI 24)".replace(",", " "),
                 xy=achats[1], xytext=(10, 20),
                 textcoords="offset points", fontsize=9, color=ENCRE,
                 arrowprops=dict(arrowstyle="->", color=ENCRE, lw=1))
    axP.annotate(f"STOP à {ventes[1][1] / achats[1][1] - 1:.0%}\n(vs -27 % sans stop)",
                 xy=ventes[1], xytext=(30, -45),
                 textcoords="offset points", fontsize=9, color=ROUGE, weight="bold",
                 arrowprops=dict(arrowstyle="->", color=ROUGE, lw=1.2))
axP.set_title("RSI + gestion du risque — le stop coupe le trade catastrophe du RSI nu",
              fontsize=12.5, weight="bold", color=ENCRE)
axP.set_ylabel("Prix ($)"); axP.yaxis.set_major_formatter(en_k)
axP.grid(alpha=0.25); axP.legend(loc="upper right", fontsize=9)

# Panneau équité : superposition (Net Profit CALCULÉ des JSON relus — jamais figé)
fr = lambda r: f"{100 * r:+.1f} %".replace(".", ",").replace("-", "−")
axE.plot(t6, e6, color=ROUGE, lw=1.3, ls="--", label=f"RSI nu : {fr(e6[-1] / e6[0] - 1)}")
axE.plot(t9, e9, color=ENCRE, lw=1.5, label=f"RSI + stop/take : {fr(e9[-1] / e9[0] - 1)}")
axE.axhline(100000, color=ENCRE, lw=1.0, ls=":", alpha=0.5)
axE.text(0.015, 0.08, "le stop protège sur le krach de janv.–févr., mais les re-stops avant rebond "
         "rognent le total : le risque doit MATCHER le signal", transform=axE.transAxes,
         fontsize=9.2, color=ENCRE, weight="bold")
axE.set_ylabel("Équité ($)"); axE.yaxis.set_major_formatter(en_k)
axE.grid(alpha=0.25); axE.legend(loc="upper right", fontsize=9)
axE.xaxis.set_major_locator(mdates.MonthLocator())
axE.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  (naif fin {e6[-1]:,.0f} / risque fin {e9[-1]:,.0f} ; {len(achats)} achats)")
