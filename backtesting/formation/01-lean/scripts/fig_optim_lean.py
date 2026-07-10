"""Figure leçon 07 (montée en gamme) : l'optimisation avec le VRAI moteur LEAN.

Remplace le proxy vectorisé : ici chaque combo est un backtest LEAN complet (outil
backtests/optimize/). Deux panneaux, tout MESURÉ (aucun chiffre figé) :

  Gauche — heatmap du rendement réel (SMA rapide × lente), lue dans le CSV du balayage.
  Droite — le démasquage IS/OOS aux DEUX visages : sur SMA le « meilleur » in-sample
           TIENT out-of-sample (un plateau robuste existe parfois), sur RSI il
           S'EFFONDRE (optimiser a nui). Même méthode, verdicts opposés.

    conda run -n backtesting python formation/01-lean/scripts/fig_optim_lean.py
"""
import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPTS = Path(__file__).resolve().parent                        # formation/01-lean/scripts
from run_grid import STRATEGIES                                   # noqa: E402  (même dossier)
from walkforward import load_runs, seg_ret, pick_champion         # noqa: E402

OUT = SCRIPTS.parents[0] / "assets" / "images" / "lecon-07-optim-lean.png"
GRID = SCRIPTS / "results" / "sma_grid.csv"
ENCRE, VERT, ROUGE, GRIS = "#263238", "#2e7d32", "#c62828", "#607d8b"


def is_oos(name, min_trades=6, split=0.5):
    """(champion, référence) mesurés : rendements IS et OOS, lus sur les courbes d'équité."""
    strat = STRATEGIES[name]
    runs, t0, t1 = load_runs(strat)
    ts = t0 + split * (t1 - t0)
    champ = pick_champion(runs, t0, ts, min_trades)
    ref = next(r for r in runs if all(r[0][k] == strat.default[k] for k in strat.default))
    seg = lambda r, a, b: seg_ret(r[1], r[2], a, b) * 100
    return {
        "champ_combo": tuple(champ[0].values()),
        "champ_is": seg(champ, t0, ts), "champ_oos": seg(champ, ts, t1),
        "ref_combo": tuple(ref[0].values()),
        "ref_is": seg(ref, t0, ts), "ref_oos": seg(ref, ts, t1),
    }


# ── données mesurées ────────────────────────────────────────────────────────
rows = list(csv.DictReader(open(GRID)))
fasts = sorted({int(r["period_fast"]) for r in rows})
slows = sorted({int(r["period_slow"]) for r in rows})
M = np.full((len(fasts), len(slows)), np.nan)
for r in rows:
    if r["status"] != "FAILED":
        M[fasts.index(int(r["period_fast"])), slows.index(int(r["period_slow"]))] = float(r["net_profit"])
bi, bj = np.unravel_index(np.nanargmax(M), M.shape)

sma, rsi = is_oos("sma"), is_oos("rsi")

# ── figure ──────────────────────────────────────────────────────────────────
fig, (axH, axB) = plt.subplots(1, 2, figsize=(13.8, 5.9))
fig.subplots_adjust(left=0.055, right=0.975, top=0.86, bottom=0.14, wspace=0.24)

im = axH.imshow(M, cmap="RdYlGn", aspect="auto", vmin=-30, vmax=30, origin="lower")
axH.set_xticks(range(len(slows))); axH.set_xticklabels(slows)
axH.set_yticks(range(len(fasts))); axH.set_yticklabels(fasts)
axH.set_xlabel("SMA lente"); axH.set_ylabel("SMA rapide")
for a in range(len(fasts)):
    for b in range(len(slows)):
        if not np.isnan(M[a, b]):
            axH.text(b, a, f"{M[a, b]:.0f}", ha="center", va="center", fontsize=7.5, color="black")
axH.add_patch(plt.Rectangle((bj - .5, bi - .5), 1, 1, fill=False, ec=ENCRE, lw=2.5))
axH.set_title(f"Balayage RÉEL LEAN — meilleur ({fasts[bi]},{slows[bj]}) à {M[bi, bj]:+.0f} %",
              fontsize=11.5, weight="bold", color=ENCRE)
fig.colorbar(im, ax=axH, fraction=0.046, pad=0.04, label="rendement plein historique (%)")

# panneau droit : IS/OOS aux deux visages
groupes = [(f"SMA champion\n{sma['champ_combo']}", sma["champ_is"], sma["champ_oos"], sma["ref_oos"], "TIENT"),
           (f"RSI champion\n{rsi['champ_combo']}", rsi["champ_is"], rsi["champ_oos"], rsi["ref_oos"], "S'EFFONDRE")]
x = np.arange(len(groupes)); w = 0.26
b1 = axB.bar(x - w, [g[1] for g in groupes], w, color="#a5d6a7", label="champion in-sample")
b2 = axB.bar(x, [g[2] for g in groupes], w, color=VERT, label="champion out-of-sample")
b3 = axB.bar(x + w, [g[3] for g in groupes], w, color=GRIS, label="référence out-of-sample")
axB.axhline(0, color=ENCRE, lw=0.8)
for bars in (b1, b2, b3):
    for bar in bars:
        h = bar.get_height()
        axB.text(bar.get_x() + bar.get_width() / 2, h + (0.5 if h >= 0 else -0.5),
                 f"{h:+.0f}", ha="center", va="bottom" if h >= 0 else "top", fontsize=8.5, weight="bold")
for i, g in enumerate(groupes):
    verdict_ok = g[2] >= g[3]
    axB.annotate(g[4], (i, max(g[1], g[2], g[3]) + 3), ha="center", fontsize=10, weight="bold",
                 color=VERT if verdict_ok else ROUGE)
axB.set_xticks(x); axB.set_xticklabels([g[0] for g in groupes], fontsize=9.5)
vals = [v for g in groupes for v in g[1:4]]
axB.set_ylim(min(vals) - 5, max(vals) + 8)
axB.set_ylabel("rendement (%)")
axB.set_title("Démasquage IS/OOS : le champion tient… ou s'effondre", fontsize=11.5, weight="bold", color=ENCRE)
axB.legend(fontsize=8.5, loc="lower left"); axB.grid(alpha=0.25, axis="y")

fig.suptitle("Optimiser avec le VRAI moteur : l'espace des paramètres, puis le juge out-of-sample",
             fontsize=12.5, weight="bold", color=ENCRE)
fig.savefig(OUT, dpi=150, facecolor="white")
print(f"OK -> {OUT}")
print(f"  heatmap meilleur : ({fasts[bi]},{slows[bj]}) = {M[bi, bj]:+.1f}%")
print(f"  SMA champion {sma['champ_combo']} : IS {sma['champ_is']:+.1f}% -> OOS {sma['champ_oos']:+.1f}% "
      f"(réf OOS {sma['ref_oos']:+.1f}%)  -> {'TIENT' if sma['champ_oos']>=sma['ref_oos'] else 'EFFONDRE'}")
print(f"  RSI champion {rsi['champ_combo']} : IS {rsi['champ_is']:+.1f}% -> OOS {rsi['champ_oos']:+.1f}% "
      f"(réf OOS {rsi['ref_oos']:+.1f}%)  -> {'TIENT' if rsi['champ_oos']>=rsi['ref_oos'] else 'EFFONDRE'}")
