"""Figure : balayage de paramètres SMA + démonstration du sur-apprentissage.

Backtest VECTORISÉ simplifié (croisement long/flat, fill au close, frais 0,04 %) —
il tombe à ~0,5 pt de LEAN sur (50,200), amplement suffisant pour COMPARER des
paramètres entre eux (c'est du relatif, pas de l'absolu).

  Gauche — heatmap du rendement sur toute la période pour une grille (rapide × lente).
           Le « meilleur » saute aux yeux… sur les données qu'il a vues.
  Droite — in-sample / out-of-sample : on optimise sur la 1re moitié, on teste sur la
           2e. Le meilleur in-sample s'effondre out-of-sample = sur-apprentissage.

    python formation/01-lean/scripts/fig_optimisation.py
"""
import csv

import matplotlib.pyplot as plt
import numpy as np

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
OUT = "formation/01-lean/assets/images/lecon-07-optimisation.png"
RAPIDES = [5, 10, 15, 20, 30, 40, 50, 60, 80, 100]
LENTES = [30, 50, 75, 100, 150, 200, 250, 300]
ENCRE, VERT, ROUGE = "#263238", "#2e7d32", "#c62828"

close = []
with open(CSV) as f:
    r = csv.reader(f); next(r)
    for row in r:
        close.append(float(row[4]))


def sma(s, n):
    out = [None] * len(s); acc = 0.0
    for i, v in enumerate(s):
        acc += v
        if i >= n: acc -= s[i - n]
        if i >= n - 1: out[i] = acc / n
    return out


SMAC = {n: sma(close, n) for n in set(RAPIDES + LENTES)}


def backtest(nf, nl, i0=0, i1=None, fee=0.0004):
    i1 = len(close) if i1 is None else i1
    rf, rl = SMAC[nf], SMAC[nl]
    eq, pos = 1.0, 0
    for i in range(i0 + 1, i1):
        if pos:
            eq *= close[i] / close[i - 1]
        state = 1 if (rf[i] is not None and rl[i] is not None and rf[i] > rl[i]) else 0
        if state != pos:
            eq *= (1 - fee); pos = state
    return eq - 1.0


# ── Heatmap sur toute la période
M = np.full((len(RAPIDES), len(LENTES)), np.nan)
for a, nf in enumerate(RAPIDES):
    for b, nl in enumerate(LENTES):
        if nf < nl:
            M[a, b] = backtest(nf, nl) * 100
best = np.unravel_index(np.nanargmax(M), M.shape)
best_nf, best_nl, best_val = RAPIDES[best[0]], LENTES[best[1]], M[best]

# ── In-sample / out-of-sample
mid = len(close) // 2
res_is = {}
for nf in RAPIDES:
    for nl in LENTES:
        if nf < nl:
            res_is[(nf, nl)] = backtest(nf, nl, 0, mid)
opt = max(res_is, key=res_is.get)               # meilleur sur la 1re moitié
opt_is = res_is[opt] * 100
opt_oos = backtest(*opt, mid, len(close)) * 100
base_is = backtest(50, 200, 0, mid) * 100
base_oos = backtest(50, 200, mid, len(close)) * 100

fig, (axH, axB) = plt.subplots(1, 2, figsize=(13.5, 5.8))
fig.subplots_adjust(left=0.06, right=0.98, top=0.88, bottom=0.13, wspace=0.28)

im = axH.imshow(M, cmap="RdYlGn", aspect="auto", vmin=-25, vmax=25)
axH.set_xticks(range(len(LENTES))); axH.set_xticklabels(LENTES)
axH.set_yticks(range(len(RAPIDES))); axH.set_yticklabels(RAPIDES)
axH.set_xlabel("SMA lente"); axH.set_ylabel("SMA rapide")
for a in range(len(RAPIDES)):
    for b in range(len(LENTES)):
        if not np.isnan(M[a, b]):
            axH.text(b, a, f"{M[a,b]:.0f}", ha="center", va="center", fontsize=7.5,
                     color="black")
axH.add_patch(plt.Rectangle((best[1] - .5, best[0] - .5), 1, 1, fill=False, ec=ENCRE, lw=2.5))
axH.set_title(f"Heatmap du rendement (%) — « meilleur » = ({best_nf},{best_nl}) à {best_val:+.0f} %",
              fontsize=11.5, weight="bold", color=ENCRE)
fig.colorbar(im, ax=axH, fraction=0.046, pad=0.04, label="rendement (%)")

x = np.arange(2); w = 0.36
b1 = axB.bar(x - w/2, [opt_is, base_is], w, color=VERT, label="in-sample (1re moitié)")
b2 = axB.bar(x + w/2, [opt_oos, base_oos], w, color=ROUGE, label="out-of-sample (2e moitié)")
axB.axhline(0, color=ENCRE, lw=0.8)
axB.set_xticks(x); axB.set_xticklabels([f"« meilleur » in-sample\n({opt[0]},{opt[1]})",
                                        "référence fixe\n(50,200)"], fontsize=9.5)
for bars in (b1, b2):
    for bar in bars:
        h = bar.get_height()
        axB.text(bar.get_x() + bar.get_width()/2, h + (1 if h >= 0 else -3),
                 f"{h:+.0f}%", ha="center", fontsize=9, weight="bold")
# ylim CALCULÉ : les étiquettes des barres négatives (h - 3) restent DANS l'axe,
# sans chevaucher les libellés de l'axe X
vals_b = [opt_is, base_is, opt_oos, base_oos]
axB.set_ylabel("rendement (%)"); axB.set_ylim(min(vals_b) - 4.5, max(vals_b) + 2.5)
axB.set_title("Le meilleur in-sample s'effondre out-of-sample", fontsize=11.5, weight="bold", color=ENCRE)
axB.legend(fontsize=9.5, loc="upper right")
axB.grid(alpha=0.25, axis="y")

fig.suptitle("Optimisation SMA : trouver le « meilleur » paramètre… et prouver qu'il est bidon",
             fontsize=13, weight="bold", color=ENCRE)
fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}")
print(f"  meilleur pleine periode : ({best_nf},{best_nl}) = {best_val:+.1f}%")
print(f"  meilleur in-sample ({opt[0]},{opt[1]}) : IS={opt_is:+.1f}%  OOS={opt_oos:+.1f}%")
print(f"  reference (50,200)      : IS={base_is:+.1f}%  OOS={base_oos:+.1f}%")
