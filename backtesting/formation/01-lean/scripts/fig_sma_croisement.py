"""Figure : la stratégie croisement SMA 50/200, en deux panneaux ALIGNÉS.

  Haut  — le PRIX horaire (relu du CSV) avec SMA 50 et SMA 200. Les trades sont
          marqués À LEUR PRIX D'EXÉCUTION RÉEL (close de la barre du croisement) :
          ▲ vert = achat, ▼ rouge = vente. La hauteur du marqueur = le vrai prix,
          donc on lit le P&L d'un aller-retour à l'œil.
  Bas   — l'ÉQUITÉ. Les bandes vertes (identiques en haut) = périodes EN POSITION ;
          ailleurs on est À PLAT (cash) donc l'équité est figée.

Les deux panneaux partagent l'axe du temps ET les mêmes marges -> grille verticale
alignée : on peut tracer un croisement du haut jusqu'à la marche d'équité du bas.

À lancer depuis la racine du projet, APRÈS le backtest (tout est relu — rien de figé) :
    python formation/01-lean/scripts/fig_sma_croisement.py
"""
import csv
import json
from datetime import datetime, timezone, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
BASE = "backtests/lean/Launcher/bin/Release/"
RESULTAT = BASE + "SmaCroisement.json"
ORDRES = BASE + "SmaCroisement-order-events.json"
OUT = "formation/01-lean/assets/images/lecon-04-sma-croisement.png"
N_RAPIDE, N_LENTE = 50, 200
ENCRE, BLEU, ORANGE, VERT, ROUGE, GRIS = "#263238", "#1d4ed8", "#e58e26", "#2e7d32", "#c62828", "#b0bec5"

# ── Prix + moyennes, recalculés du CSV — tracés à la CLÔTURE de leur barre
# (l'instant où close et SMA sont connaissables ; les fills sont déjà à cet instant)
t, close = [], []
with open(CSV) as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        t.append(datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone.utc) + timedelta(hours=1))
        close.append(float(row[4]))


def sma(serie, n):
    out, s = [None] * len(serie), 0.0
    for i, v in enumerate(serie):
        s += v
        if i >= n:
            s -= serie[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


rapide, lente = sma(close, N_RAPIDE), sma(close, N_LENTE)

# ── Trades réels (prix ET instant d'exécution) depuis le journal d'ordres
fills = [e for e in json.load(open(ORDRES)) if e["status"] == "filled"]
def dt(e): return datetime.fromtimestamp(e["time"], tz=timezone.utc)
achats = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "buy"]
ventes = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "sell"]
positions = list(zip([a[0] for a in achats], [v[0] for v in ventes]))  # paires achat->vente

# ── Équité
d = json.load(open(RESULTAT))
vals = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
te = [datetime.fromtimestamp(v[0], tz=timezone.utc) for v in vals]
equite = [v[4] for v in vals]
depart, fin = equite[0], equite[-1]
pic, dd_max = equite[0], 0.0
for e in equite:
    pic = max(pic, e)
    dd_max = max(dd_max, 1 - e / pic)

# ── Tracé : marges FIGÉES -> les deux panneaux ont exactement la même étendue X
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8.4), sharex=True,
                               gridspec_kw={"height_ratios": [2.1, 1]})
fig.subplots_adjust(left=0.075, right=0.975, top=0.93, bottom=0.075, hspace=0.13)
en_k = FuncFormatter(lambda v, _: f"{v/1000:.0f}k")     # libellés Y courts et de largeur égale

for i, (deb, fin_p) in enumerate(positions):            # mêmes bandes sur les DEUX panneaux
    for ax in (ax1, ax2):
        ax.axvspan(deb, fin_p, color=VERT, alpha=0.12,
                   label="en position (long)" if (i == 0 and ax is ax2) else None)

# -- Panneau du haut : prix + moyennes + trades AU PRIX RÉEL
ax1.plot(t, close, color=GRIS, lw=0.8, alpha=0.9, label="Prix BTCUSDT (close 1 h)")
ax1.plot(t, rapide, color=BLEU, lw=1.5, label=f"SMA {N_RAPIDE} (rapide)")
ax1.plot(t, lente, color=ORANGE, lw=1.7, label=f"SMA {N_LENTE} (lente)")
ax1.plot([a[0] for a in achats], [a[1] for a in achats], "^", ms=12, mfc=VERT,
         mec="white", mew=1.3, zorder=6, label=f"achat ▲ ×{len(achats)} (au prix réel)")
ax1.plot([v[0] for v in ventes], [v[1] for v in ventes], "v", ms=12, mfc=ROUGE,
         mec="white", mew=1.3, zorder=6, label=f"vente ▼ ×{len(ventes)} (au prix réel)")
ax1.set_title(f"Croisement SMA {N_RAPIDE}/{N_LENTE} — trades marqués à leur prix d'exécution réel",
              fontsize=12.5, weight="bold", color=ENCRE)
ax1.set_ylabel("Prix ($)")
ax1.yaxis.set_major_formatter(en_k)
ax1.grid(alpha=0.25)
ax1.legend(loc="upper right", fontsize=8.5, ncol=2)

# -- Panneau du bas : équité
ax2.plot(te, equite, color=ENCRE, lw=1.4, label="Équité ($)")
ax2.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.5)
ax2.plot(te[-1], fin, "o", ms=7, mfc=ENCRE, mec="white", mew=1.3, zorder=6)
ax2.annotate(f"{fin:,.0f} $ ({fin / depart - 1:+.2%})", xy=(te[-1], fin),
             xytext=(-140, -30), textcoords="offset points", fontsize=9.5,
             weight="bold", color=ENCRE,
             arrowprops=dict(arrowstyle="->", lw=1.3, color=ENCRE,
                             connectionstyle="arc3,rad=-0.2"))
ax2.text(0.015, 0.09, f"bandes vertes = EN POSITION · ailleurs À PLAT (cash) → équité figée  ·  "
         f"drawdown max {dd_max:.1%} (vs 40 % en Buy & Hold)",
         transform=ax2.transAxes, fontsize=9.5, color=ROUGE, weight="bold")
ax2.set_ylabel("Équité ($)")
ax2.yaxis.set_major_formatter(en_k)
ax2.grid(alpha=0.25)
ax2.legend(loc="upper right", fontsize=9)
ax2.xaxis.set_major_locator(mdates.MonthLocator())
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  ({len(achats)} achats / {len(ventes)} ventes au prix réel, "
      f"équité {depart:,.0f} -> {fin:,.0f} $, drawdown max {dd_max:.1%})")
