"""Figure : Bollinger 20/2σ, retour à la moyenne ADAPTATIF, en trois panneaux alignés.

  Haut   — PRIX + les 3 bandes (haute / médiane=SMA20 / basse). L'enveloppe se resserre
           et s'élargit avec la volatilité. Trades au prix réel ; bandes vertes = détention.
  Milieu — LARGEUR DE BANDE % = (haute-basse)/médiane : la mesure de volatilité elle-même.
           Le pic de février = le krach ; les creux = marché calme.
  Bas    — ÉQUITÉ : 155 trades, l'érosion par les frais.

Marges figées -> mêmes axes X et grille alignés.
    python formation/01-lean/scripts/fig_bollinger.py
"""
import csv
import json
from datetime import datetime, timezone, timedelta
from statistics import pstdev

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
BASE = "backtests/lean/Launcher/bin/Release/"
RESULTAT = BASE + "Bollinger.json"
ORDRES = BASE + "Bollinger-order-events.json"
OUT = "formation/01-lean/assets/images/lecon-05-bollinger.png"
N_BB, K = 20, 2.0
ENCRE, BLEU, ORANGE, VERT, ROUGE, GRIS, VIOLET = "#263238", "#1d4ed8", "#e58e26", "#2e7d32", "#c62828", "#b0bec5", "#6a1b9a"

# ── Prix + bandes recalculées du CSV (écart-type de population, comme LEAN) — tracés
# à la CLÔTURE de leur barre (l'instant où close et bandes sont connaissables)
t, close = [], []
with open(CSV) as f:
    r = csv.reader(f); next(r)
    for row in r:
        t.append(datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone.utc) + timedelta(hours=1))
        close.append(float(row[4]))

haute, med, basse, largeur = [None]*len(close), [None]*len(close), [None]*len(close), [None]*len(close)
for i in range(N_BB - 1, len(close)):
    fen = close[i - N_BB + 1:i + 1]
    m = sum(fen) / N_BB
    sd = pstdev(fen)
    med[i] = m; haute[i] = m + K * sd; basse[i] = m - K * sd
    largeur[i] = (haute[i] - basse[i]) / m * 100      # largeur en % du prix

nan = float("nan")
haute = [x if x is not None else nan for x in haute]
med = [x if x is not None else nan for x in med]
basse = [x if x is not None else nan for x in basse]
largeur = [x if x is not None else nan for x in largeur]

# ── Trades réels + équité
fills = [e for e in json.load(open(ORDRES)) if e["status"] == "filled"]
def dt(e): return datetime.fromtimestamp(e["time"], tz=timezone.utc)
achats = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "buy"]
ventes = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "sell"]
positions = []
for k, a in enumerate(achats):
    fin_p = ventes[k][0] if k < len(ventes) else t[-1]
    positions.append((a[0], fin_p))

d = json.load(open(RESULTAT))
vals = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
te = [datetime.fromtimestamp(v[0], tz=timezone.utc) for v in vals]
equite = [v[4] for v in vals]
depart, fin = equite[0], equite[-1]
pic, dd_max = equite[0], 0.0
for e in equite:
    pic = max(pic, e); dd_max = max(dd_max, 1 - e / pic)
frais = d["statistics"]["Total Fees"] if "statistics" in d else "?"

fig, (axP, axW, axE) = plt.subplots(3, 1, figsize=(12, 10), sharex=True,
                                    gridspec_kw={"height_ratios": [2, 1, 1.25]})
fig.subplots_adjust(left=0.075, right=0.975, top=0.95, bottom=0.06, hspace=0.12)
en_k = FuncFormatter(lambda v, _: f"{v/1000:.0f}k")

for i, (deb, fin_p) in enumerate(positions):
    for ax in (axP, axE):
        ax.axvspan(deb, fin_p, color=VERT, alpha=0.10,
                   label="en position (long)" if (i == 0 and ax is axE) else None)

# Panneau prix + bandes
axP.fill_between(t, basse, haute, color=BLEU, alpha=0.06)
axP.plot(t, close, color=GRIS, lw=0.8, alpha=0.9, label="Prix (close 1 h)")
axP.plot(t, haute, color=BLEU, lw=1.0, alpha=0.8, label=f"bande haute (+{K:.0f}σ)")
axP.plot(t, med, color=ORANGE, lw=1.3, label=f"médiane (SMA {N_BB})")
axP.plot(t, basse, color=BLEU, lw=1.0, alpha=0.8, ls="--", label=f"bande basse (−{K:.0f}σ)")
axP.plot([a[0] for a in achats], [a[1] for a in achats], "^", ms=6, mfc=VERT,
         mec="white", mew=0.6, zorder=6, label=f"achat ▲ ×{len(achats)}")
axP.plot([v[0] for v in ventes], [v[1] for v in ventes], "v", ms=6, mfc=ROUGE,
         mec="white", mew=0.6, zorder=6, label=f"vente ▼ ×{len(ventes)}")
axP.set_title(f"Bollinger {N_BB}/{K:.0f}σ — acheter sous la bande basse, sortir au retour à la médiane",
              fontsize=12.5, weight="bold", color=ENCRE)
axP.set_ylabel("Prix ($)"); axP.yaxis.set_major_formatter(en_k)
axP.grid(alpha=0.25); axP.legend(loc="upper right", fontsize=8, ncol=2)

# Panneau largeur de bande (volatilité)
axW.fill_between(t, [0]*len(largeur), [0 if x != x else x for x in largeur], color=VIOLET, alpha=0.20)
axW.plot(t, largeur, color=VIOLET, lw=1.0)
i_max = max((i for i in range(len(largeur)) if largeur[i] == largeur[i]), key=lambda i: largeur[i])
axW.annotate(f"krach : bandes à {largeur[i_max]:.0f} % du prix", xy=(t[i_max], largeur[i_max]),
             xytext=(20, -4), textcoords="offset points", fontsize=9, color=VIOLET, weight="bold")
axW.set_ylabel("largeur bandes\n(% du prix)"); axW.grid(alpha=0.25)

# Panneau équité
axE.plot(te, equite, color=ENCRE, lw=1.3, label="Équité ($)")
axE.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.5)
axE.plot(te[-1], fin, "o", ms=7, mfc=ENCRE, mec="white", mew=1.3, zorder=6)
axE.annotate(f"{fin:,.0f} $ ({fin / depart - 1:+.2%})", xy=(te[-1], fin),
             xytext=(-150, 24), textcoords="offset points", fontsize=9.5,
             weight="bold", color=ENCRE,
             arrowprops=dict(arrowstyle="->", lw=1.3, color=ENCRE, connectionstyle="arc3,rad=0.2"))
axE.text(0.015, 0.10, f"{len(fills)} trades → {frais} de frais (~5 % du capital) : le sur-trading tue la stratégie  ·  "
         f"drawdown max {dd_max:.1%}", transform=axE.transAxes, fontsize=9.5, color=ROUGE, weight="bold")
axE.set_ylabel("Équité ($)"); axE.yaxis.set_major_formatter(en_k)
axE.grid(alpha=0.25); axE.legend(loc="upper right", fontsize=9)
axE.xaxis.set_major_locator(mdates.MonthLocator())
axE.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  ({len(achats)} achats / {len(ventes)} ventes, largeur max {largeur[i_max]:.0f} %, "
      f"équité {depart:,.0f} -> {fin:,.0f} $, dd max {dd_max:.1%})")
