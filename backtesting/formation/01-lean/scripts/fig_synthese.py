"""Figure de synthèse : les sept stratégies de la section LEAN, une seule vue.

Superpose les courbes d'équité (relues des *-summary/JSON) pour clore la section :
sur ce marché baissier, seules survivent à peu près les stratégies qui savent SORTIR
(SMA 50/200) ou qui n'entrent qu'en régime favorable avec un risque taillé
(stratégie avancée : −3,3 %, drawdown 8 % contre 40 % en Buy & Hold).

    python formation/01-lean/scripts/fig_synthese.py
"""
import json
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

BASE = "backtests/lean/Launcher/bin/Release/"
OUT = "formation/01-lean/assets/images/lecon-08-synthese.png"
STRATS = [
    ("StrategieAvancee", "Stratégie avancée", "#263238", 2.4, "-"),
    ("SmaCroisement", "SMA 50/200 (tendance)", "#2e7d32", 2.0, "-"),
    ("RsiRetourMoyenne", "RSI 14 (contre-tendance)", "#1d4ed8", 1.3, "-"),
    ("RisqueStops", "RSI + risque", "#6a1b9a", 1.3, "-"),
    ("Bollinger", "Bollinger", "#e58e26", 1.3, "-"),
    ("Macd", "MACD", "#c62828", 1.3, "-"),
    ("BuyHold", "Buy & Hold", "#607d8b", 2.0, "--"),
]
ENCRE = "#263238"


def equity(cls):
    d = json.load(open(BASE + cls + ".json"))
    v = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
    return [datetime.fromtimestamp(x[0], tz=timezone.utc) for x in v], [x[4] for x in v]


fig, ax = plt.subplots(figsize=(12, 6.2))
fig.subplots_adjust(left=0.08, right=0.975, top=0.9, bottom=0.1)

# Net Profit CALCULÉ des JSON relus (jamais figé), légende triée du meilleur au pire
courbes = []
for cls, nom, coul, lw, ls in STRATS:
    t, e = equity(cls)
    courbes.append((e[-1] / e[0] - 1, nom, coul, lw, ls, t, e))
courbes.sort(key=lambda c: c[0], reverse=True)
for r, nom, coul, lw, ls, t, e in courbes:
    pct = f"{100 * r:+.1f} %".replace(".", ",").replace("-", "−")
    ax.plot(t, e, color=coul, lw=lw, ls=ls, label=f"{nom:<25}{pct:>8}")

ax.axhline(100000, color=ENCRE, lw=1.0, ls=":", alpha=0.6)
ax.text(0.012, 0.94, "capital de départ 100 000 $", transform=ax.transAxes, fontsize=9,
        color=ENCRE, va="top")
ax.set_title("Section LEAN — sept stratégies, un marché baissier : régime + risque limitent la casse",
             fontsize=12.5, weight="bold", color=ENCRE)
ax.set_ylabel("Équité ($)"); ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
ax.grid(alpha=0.25)
ax.legend(loc="lower left", fontsize=9.5, ncol=1, framealpha=0.92,
          title="stratégie (Net Profit)", title_fontsize=9.5, prop={"family": "monospace"})
ax.xaxis.set_major_locator(mdates.MonthLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}")
