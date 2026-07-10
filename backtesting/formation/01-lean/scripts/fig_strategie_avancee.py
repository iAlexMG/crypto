"""Figure : la stratégie avancée (capstone) — régime + momentum + risque, deux panneaux ALIGNÉS.

  Haut — le PRIX horaire (relu du CSV) avec la SMA 200 (le filtre de RÉGIME : on n'achète
         jamais sous elle). Les trades sont marqués À LEUR PRIX D'EXÉCUTION RÉEL :
         ▲ vert = achat, ▼ rouge = vente. Bandes vertes = périodes EN POSITION.
  Bas  — l'ÉQUITÉ de la stratégie (trait plein) face au Buy & Hold (pointillé) :
         mêmes bandes vertes ; ailleurs on est À PLAT (cash), l'équité est figée.

Les deux panneaux partagent l'axe du temps ET les mêmes marges -> grille alignée.

À lancer depuis la racine du projet, APRÈS les backtests StrategieAvancee et BuyHold :
    python formation/01-lean/scripts/fig_strategie_avancee.py
"""
import csv
import json
from datetime import datetime, timezone, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
BASE = "backtests/lean/Launcher/bin/Release/"
RESULTAT = BASE + "StrategieAvancee.json"
ORDRES = BASE + "StrategieAvancee-order-events.json"
REFERENCE = BASE + "BuyHold.json"
OUT = "formation/01-lean/assets/images/lecon-08-strategie-avancee.png"
N_TENDANCE = 200
ENCRE, ORANGE, VERT, ROUGE, GRIS = "#263238", "#e58e26", "#2e7d32", "#c62828", "#b0bec5"

# ── Prix + SMA de régime, recalculés du CSV — tracés à la CLÔTURE de leur barre
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


tendance = sma(close, N_TENDANCE)

# ── Trades réels (prix ET instant d'exécution) depuis le journal d'ordres
fills = [e for e in json.load(open(ORDRES)) if e["status"] == "filled"]
def dt(e): return datetime.fromtimestamp(e["time"], tz=timezone.utc)
achats = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "buy"]
ventes = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "sell"]
positions = list(zip([a[0] for a in achats], [v[0] for v in ventes]))

# ── Équités (stratégie + référence Buy & Hold)
def equity(path):
    d = json.load(open(path))
    v = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
    return ([datetime.fromtimestamp(x[0], tz=timezone.utc) for x in v], [x[4] for x in v])

te, eq = equity(RESULTAT)
tb, eb = equity(REFERENCE)
depart, fin = eq[0], eq[-1]
pic, dd_max = eq[0], 0.0
for e in eq:
    pic = max(pic, e)
    dd_max = max(dd_max, 1 - e / pic)

# ── Tracé : marges FIGÉES -> les deux panneaux ont exactement la même étendue X
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8.4), sharex=True,
                               gridspec_kw={"height_ratios": [2.1, 1]})
fig.subplots_adjust(left=0.075, right=0.975, top=0.93, bottom=0.075, hspace=0.13)
en_k = FuncFormatter(lambda v, _: f"{v/1000:.0f}k")

for i, (deb, fin_p) in enumerate(positions):
    for ax in (ax1, ax2):
        ax.axvspan(deb, fin_p, color=VERT, alpha=0.12,
                   label="en position (long)" if (i == 0 and ax is ax2) else None)

# -- Panneau du haut : prix + SMA 200 (régime) + trades AU PRIX RÉEL
ax1.plot(t, close, color=GRIS, lw=0.8, alpha=0.9, label="Prix BTCUSDT (close 1 h)")
ax1.plot(t, tendance, color=ORANGE, lw=1.8, label=f"SMA {N_TENDANCE} (filtre de régime)")
ax1.plot([a[0] for a in achats], [a[1] for a in achats], "^", ms=9, mfc=VERT,
         mec="white", mew=1.1, zorder=6, label=f"achat ▲ ×{len(achats)} (au prix réel)")
ax1.plot([v[0] for v in ventes], [v[1] for v in ventes], "v", ms=9, mfc=ROUGE,
         mec="white", mew=1.1, zorder=6, label=f"vente ▼ ×{len(ventes)} (au prix réel)")
ax1.set_title("Stratégie avancée — régime (SMA 200) + croisement MACD/RSI + sizing au risque + stops ATR",
              fontsize=12.5, weight="bold", color=ENCRE)
ax1.set_ylabel("Prix ($)")
ax1.yaxis.set_major_formatter(en_k)
ax1.grid(alpha=0.25)
ax1.legend(loc="upper right", fontsize=8.5, ncol=2)

# -- Panneau du bas : équité stratégie vs Buy & Hold (Net Profit CALCULÉ, jamais figé)
fr = lambda r: f"{100 * r:+.1f} %".replace(".", ",").replace("-", "−")
ax2.plot(tb, eb, color=GRIS, lw=1.4, ls="--", label=f"Buy & Hold : {fr(eb[-1] / eb[0] - 1)}")
ax2.plot(te, eq, color=ENCRE, lw=1.5, label="Stratégie avancée ($)")
ax2.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.5)
ax2.plot(te[-1], fin, "o", ms=7, mfc=ENCRE, mec="white", mew=1.3, zorder=6)
# annotation SOUS la courbe (le coin haut-droit est occupé par le texte drawdown)
ax2.annotate(f"{fin:,.0f} $ ({fin / depart - 1:+.2%})", xy=(te[-1], fin),
             xytext=(-165, -38), textcoords="offset points", fontsize=9.5,
             weight="bold", color=ENCRE,
             arrowprops=dict(arrowstyle="->", lw=1.3, color=ENCRE,
                             connectionstyle="arc3,rad=-0.25"))
ax2.text(0.99, 0.90, f"bandes vertes = EN POSITION · ailleurs À PLAT (cash) → équité figée  ·  "
         f"drawdown max {dd_max:.1%} (vs 40 % en Buy & Hold)",
         transform=ax2.transAxes, fontsize=9.5, color=ROUGE, weight="bold",
         ha="right", va="top")
ax2.set_ylabel("Équité ($)")
ax2.yaxis.set_major_formatter(en_k)
ax2.grid(alpha=0.25)
ax2.legend(loc="lower left", fontsize=9)
ax2.xaxis.set_major_locator(mdates.MonthLocator())
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  ({len(achats)} achats / {len(ventes)} ventes au prix réel, "
      f"équité {depart:,.0f} -> {fin:,.0f} $, drawdown max {dd_max:.1%})")
