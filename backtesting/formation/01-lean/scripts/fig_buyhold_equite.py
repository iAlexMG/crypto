"""Figure : courbe d'équité du Buy & Hold BTCUSDT, lue dans le JSON de résultats LEAN.

À lancer depuis la racine du projet, APRÈS le backtest (le JSON est un
produit du run — chiffres jamais figés, tout est relu depuis le fichier) :
    python formation/01-lean/scripts/fig_buyhold_equite.py
"""
import json
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

RESULTAT = "backtests/lean/Launcher/bin/Release/BuyHold.json"
OUT = "formation/01-lean/assets/images/lecon-03-buyhold-equite.png"
ENCRE, BLEU, ROUGE, AMBRE = "#263238", "#1d4ed8", "#c62828", "#b45309"

d = json.load(open(RESULTAT))
# Série "Equity" = chandelles [timestamp_unix, open, high, low, close] échantillonnées
vals = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
t = [datetime.fromtimestamp(v[0], tz=timezone.utc) for v in vals]
equite = [v[4] for v in vals]

depart, fin = equite[0], equite[-1]
# pic courant (plus-haut historique) et drawdown max — recalculés, jamais figés
pics, pic_courant, dd_max = [], equite[0], 0.0
for e in equite:
    pic_courant = max(pic_courant, e)
    pics.append(pic_courant)
    dd_max = max(dd_max, 1 - e / pic_courant)
i_pic = max(range(len(equite)), key=lambda i: equite[i])

fig, ax = plt.subplots(figsize=(11, 5.2))
ax.fill_between(t, pics, equite, color=ROUGE, alpha=0.10,
                label="sous l'eau (écart au plus-haut)")
ax.plot(t, equite, color=BLEU, lw=1.6, label="Équité du portefeuille ($)")
ax.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.55)
ax.text(t[2], depart, f"  capital de départ {depart:,.0f} $", fontsize=9.5,
        color=ENCRE, va="bottom")

ax.plot(t[i_pic], equite[i_pic], "o", ms=7, mfc=AMBRE, mec="white", mew=1.2, zorder=6)
ax.annotate(f"plus-haut {equite[i_pic]:,.0f} $ ({equite[i_pic] / depart - 1:+.1%})",
            xy=(t[i_pic], equite[i_pic]), xytext=(18, 6), textcoords="offset points",
            fontsize=9.5, color=AMBRE, weight="bold")
ax.plot(t[-1], fin, "o", ms=8, mfc=ROUGE, mec="white", mew=1.4, zorder=6)
ax.annotate(f"équité finale {fin:,.0f} $ ({fin / depart - 1:+.2%})",
            xy=(t[-1], fin), xytext=(-215, 42), textcoords="offset points",
            fontsize=10, weight="bold", color=ROUGE,
            arrowprops=dict(arrowstyle="->", lw=1.5, color=ROUGE,
                            connectionstyle="arc3,rad=0.25"))
ax.text(0.13, 0.06, f"drawdown max : {dd_max:.1%} depuis le plus-haut",
        transform=ax.transAxes, fontsize=10.5, color=ROUGE, weight="bold")

ax.set_title("Buy & Hold BTCUSDT — l'équité N'EST PAS une ligne droite vers le close final",
             fontsize=12, weight="bold", color=ENCRE)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
ax.set_ylabel("Équité ($)")
ax.grid(alpha=0.25)
ax.legend(loc="upper right", fontsize=9.5)
fig.tight_layout()
fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  (équité {depart:,.0f} -> {fin:,.0f} $, drawdown max {dd_max:.1%})")
