"""Figure : lire (et se méfier de) les métriques, sur nos 6 stratégies.

  Gauche — le PLAN RISQUE-RENDEMENT : Net Profit vs drawdown max. Le bon coin est
           en haut à gauche (peu de perte, peu de douleur). La SMA 50/200 y domine.
  Droite — le PIÈGE DU WIN RATE : Win Rate vs Net Profit. Aucune relation — le RSI a
           le meilleur taux de réussite (60 %) et perd le plus des stratégies « douces ».

Lit les *-summary.json produits par les backtests des 6 stratégies.
    python formation/01-lean/scripts/fig_metriques.py
"""
import json

import matplotlib.pyplot as plt

BASE = "backtests/lean/Launcher/bin/Release/"
STRATS = [
    ("BuyHold", "Buy & Hold", "#607d8b"),
    ("SmaCroisement", "SMA 50/200", "#2e7d32"),
    ("RsiRetourMoyenne", "RSI 14", "#1d4ed8"),
    ("Bollinger", "Bollinger", "#e58e26"),
    ("Macd", "MACD", "#c62828"),
    ("RisqueStops", "RSI+risque", "#6a1b9a"),
]
OUT = "formation/01-lean/assets/images/lecon-07-metriques.png"
ENCRE = "#263238"


def pct(s):
    return float(str(s).replace("%", "").replace("$", "").replace(",", ""))


data = []
for cls, nom, coul in STRATS:
    d = json.load(open(BASE + cls + "-summary.json"))["statistics"]
    data.append(dict(nom=nom, coul=coul,
                     net=pct(d["Net Profit"]), dd=pct(d["Drawdown"]),
                     win=pct(d["Win Rate"]), sharpe=pct(d["Sharpe Ratio"])))

fig, (axA, axB) = plt.subplots(1, 2, figsize=(13, 5.6))
fig.subplots_adjust(left=0.07, right=0.975, top=0.9, bottom=0.12, wspace=0.22)

# ── Plan risque-rendement
for s in data:
    axA.scatter(s["dd"], s["net"], s=160, color=s["coul"], zorder=5, ec="white", lw=1.2)
    axA.annotate(s["nom"], (s["dd"], s["net"]), xytext=(8, 6), textcoords="offset points",
                 fontsize=9.5, weight="bold", color=s["coul"])
sma_pt = next(s for s in data if s["nom"] == "SMA 50/200")   # cible d'annotation relue
axA.annotate("le bon coin :\npeu de perte, peu de douleur", xy=(sma_pt["dd"], sma_pt["net"]),
             xytext=(70, -30),
             textcoords="offset points", fontsize=9, color="#2e7d32", weight="bold",
             arrowprops=dict(arrowstyle="->", color="#2e7d32", lw=1.2))
axA.set_xlabel("Drawdown max (%) — la douleur →"); axA.set_ylabel("Net Profit (%) — le résultat ↑")
axA.set_title("Plan risque-rendement : la SMA 50/200 domine", fontsize=11.5, weight="bold", color=ENCRE)
axA.grid(alpha=0.25)

# ── Le piège du win rate
for s in data:
    axB.scatter(s["win"], s["net"], s=160, color=s["coul"], zorder=5, ec="white", lw=1.2)
    axB.annotate(s["nom"], (s["win"], s["net"]), xytext=(8, 6), textcoords="offset points",
                 fontsize=9.5, weight="bold", color=s["coul"])
axB.axhline(0, color=ENCRE, lw=0.8, ls="--", alpha=0.5)
rsi_pt = next(s for s in data if s["nom"] == "RSI 14")       # cible d'annotation relue
axB.annotate(f"{rsi_pt['win']:.0f} % de trades gagnants…\net pourtant "
             f"{rsi_pt['net']:.0f} % !".replace("-", "−"),
             xy=(rsi_pt["win"], rsi_pt["net"]), xytext=(-150, 34),
             textcoords="offset points", fontsize=9.5, color="#1d4ed8", weight="bold",
             arrowprops=dict(arrowstyle="->", color="#1d4ed8", lw=1.2))
axB.set_xlabel("Win Rate (% de trades gagnants) →"); axB.set_ylabel("Net Profit (%) ↑")
axB.set_title("Le piège du win rate : aucune relation avec le résultat", fontsize=11.5, weight="bold", color=ENCRE)
axB.grid(alpha=0.25)

fig.suptitle("Six stratégies, mêmes données — ce que les métriques disent, et ce qu'elles cachent",
             fontsize=12.5, weight="bold", color=ENCRE)
fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}")
for s in data:
    print(f'  {s["nom"]:12} net={s["net"]:+7.2f}% dd={s["dd"]:5.1f}% win={s["win"]:4.0f}% sharpe={s["sharpe"]:+.2f}')
