"""Figure : MACD(12,26,9), suivi de tendance RÉACTIF, en trois panneaux alignés.

  Haut   — PRIX + EMA 12 (rapide) et EMA 26 (lente) ; trades au prix réel ; bandes de détention.
  Milieu — le MACD lui-même : ligne MACD (EMA12-EMA26), ligne de signal (EMA9 du MACD),
           et l'HISTOGRAMME (MACD - signal). L'histogramme change de signe AU croisement =
           le déclencheur des ordres.
  Bas    — ÉQUITÉ : 341 trades, l'anéantissement par les frais.

Marges figées -> mêmes axes X et grille.
    python formation/01-lean/scripts/fig_macd.py
"""
import csv
import json
from datetime import datetime, timezone, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
BASE = "backtests/lean/Launcher/bin/Release/"
RESULTAT = BASE + "Macd.json"
ORDRES = BASE + "Macd-order-events.json"
OUT = "formation/01-lean/assets/images/lecon-04-macd.png"
RAPIDE, LENTE, SIGNAL = 12, 26, 9
ENCRE, BLEU, ORANGE, VERT, ROUGE, GRIS = "#263238", "#1d4ed8", "#e58e26", "#2e7d32", "#c62828", "#b0bec5"

# Prix et indicateurs tracés à la CLÔTURE de leur barre (l'instant où ils sont connaissables)
t, close = [], []
with open(CSV) as f:
    r = csv.reader(f); next(r)
    for row in r:
        t.append(datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone.utc) + timedelta(hours=1))
        close.append(float(row[4]))


def ema(serie, n):
    a = 2 / (n + 1)
    out = [None] * len(serie)
    e = serie[0]
    for i, v in enumerate(serie):
        e = v if i == 0 else a * v + (1 - a) * e
        out[i] = e
    return out


e12, e26 = ema(close, RAPIDE), ema(close, LENTE)
macd = [e12[i] - e26[i] for i in range(len(close))]
signal = ema(macd, SIGNAL)
hist = [macd[i] - signal[i] for i in range(len(close))]

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

fig, (axP, axM, axE) = plt.subplots(3, 1, figsize=(12, 10), sharex=True,
                                    gridspec_kw={"height_ratios": [2, 1.15, 1.15]})
fig.subplots_adjust(left=0.08, right=0.975, top=0.95, bottom=0.06, hspace=0.12)
en_k = FuncFormatter(lambda v, _: f"{v/1000:.0f}k")

for i, (deb, fin_p) in enumerate(positions):
    for ax in (axP, axE):
        ax.axvspan(deb, fin_p, color=VERT, alpha=0.08,
                   label="en position (long)" if (i == 0 and ax is axE) else None)

# Panneau prix + EMA
axP.plot(t, close, color=GRIS, lw=0.8, alpha=0.9, label="Prix (close 1 h)")
axP.plot(t, e12, color=BLEU, lw=1.2, label=f"EMA {RAPIDE} (rapide)")
axP.plot(t, e26, color=ORANGE, lw=1.4, label=f"EMA {LENTE} (lente)")
axP.plot([a[0] for a in achats], [a[1] for a in achats], "^", ms=4, mfc=VERT,
         mec="none", zorder=6, label=f"achat ▲ ×{len(achats)}")
axP.plot([v[0] for v in ventes], [v[1] for v in ventes], "v", ms=4, mfc=ROUGE,
         mec="none", zorder=6, label=f"vente ▼ ×{len(ventes)}")
axP.set_title(f"MACD ({RAPIDE},{LENTE},{SIGNAL}) — suivi de tendance RÉACTIF : chaque frémissement = un ordre",
              fontsize=12.5, weight="bold", color=ENCRE)
axP.set_ylabel("Prix ($)"); axP.yaxis.set_major_formatter(en_k)
axP.grid(alpha=0.25); axP.legend(loc="upper right", fontsize=8, ncol=2)

# Panneau MACD (ligne + signal + histogramme)
coul_hist = [VERT if h >= 0 else ROUGE for h in hist]
axM.bar(t, hist, width=0.04, color=coul_hist, alpha=0.35, label="histogramme (MACD − signal)")
axM.plot(t, macd, color=BLEU, lw=1.0, label="ligne MACD")
axM.plot(t, signal, color=ORANGE, lw=1.0, label="ligne de signal")
axM.axhline(0, color=ENCRE, lw=0.9, alpha=0.6)
axM.set_ylabel("MACD ($)"); axM.grid(alpha=0.25); axM.legend(loc="upper right", fontsize=8, ncol=3)

# Panneau équité
axE.plot(te, equite, color=ENCRE, lw=1.3, label="Équité ($)")
axE.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.5)
axE.plot(te[-1], fin, "o", ms=7, mfc=ENCRE, mec="white", mew=1.3, zorder=6)
axE.annotate(f"{fin:,.0f} $ ({fin / depart - 1:+.2%})", xy=(te[-1], fin),
             xytext=(-150, 24), textcoords="offset points", fontsize=9.5,
             weight="bold", color=ENCRE,
             arrowprops=dict(arrowstyle="->", lw=1.3, color=ENCRE, connectionstyle="arc3,rad=0.2"))
axE.text(0.015, 0.10, f"{len(fills)} trades → {frais} de frais (~12 % du capital !) : la réactivité "
         f"sur données bruitées = whipsaws  ·  drawdown max {dd_max:.1%}",
         transform=axE.transAxes, fontsize=9.5, color=ROUGE, weight="bold")
axE.set_ylabel("Équité ($)"); axE.yaxis.set_major_formatter(en_k)
axE.grid(alpha=0.25); axE.legend(loc="upper right", fontsize=9)
axE.xaxis.set_major_locator(mdates.MonthLocator())
axE.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  ({len(achats)} achats / {len(ventes)} ventes, "
      f"équité {depart:,.0f} -> {fin:,.0f} $, dd max {dd_max:.1%})")
