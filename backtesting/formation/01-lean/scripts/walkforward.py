"""IS/OOS et walk-forward : le juge anti-sur-apprentissage.

Réutilise les runs de run_grid.py — les courbes d'équité sont DÉJÀ calculées, donc
AUCUN re-run LEAN. On découpe chaque courbe d'équité à une date-frontière et on compare
le RENDEMENT in-sample (IS = données vues par l'optimiseur) au rendement out-of-sample
(OOS = réservé, jamais vu). Rappel des sigles : IS = In-Sample, OOS = Out-Of-Sample.

  --split F  (défaut 0.5) : coupe en DEUX. Optimise sur [0,F], juge sur [F,1]. Montre si
                            le « meilleur » IS s'effondre en OOS (la démo de la leçon 07).
  --folds K               : WALK-FORWARD ancré. À chaque pas, optimise sur tout le passé
                            et teste sur le fold suivant -> rendement OOS enchaîné, comme
                            si on re-réglait périodiquement en conditions réelles.
  --min-trades N          : n'« optimise » que parmi les combos ayant fait >= N trades.
                            Écarte les réglages DÉGÉNÉRÉS (0 trade -> reste à plat -> 0 %,
                            qui « gagnent » sur un marché baissier en ne jouant pas).

Métrique = RENDEMENT par segment (net, lu sur la courbe d'équité) — exactement la mesure
de la leçon 07. Le combo de référence est le réglage de la formation (Strategy.default).

Exemples
    python backtests/optimize/walkforward.py sma
    python backtests/optimize/walkforward.py rsi --min-trades 6
    python backtests/optimize/walkforward.py sma --folds 6
"""
from __future__ import annotations

import argparse
import bisect
import json
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np

from run_grid import RESULTS_DIR, RUNS_DIR, STRATEGIES, combo_id

ENCRE, BLEU = "#263238", "#1d4ed8"


# ── Lecture des runs (courbe d'équité + nb de trades), déjà produits par run_grid.py ──
def load_run(strat, combo):
    path = RUNS_DIR / strat.name / combo_id(combo) / f"{strat.class_name}.json"
    dd = json.load(open(path, encoding="utf-8"))
    vals = dd["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
    ts = [int(v[0]) for v in vals]      # timestamp unix
    eq = [float(v[4]) for v in vals]    # équité (close de la bougie d'équité)
    trades = int(float(dd["statistics"].get("Total Orders", 0)))
    return ts, eq, trades


def eq_at(ts, eq, t):
    """Équité à l'instant t = dernière valeur mesurée <= t."""
    i = bisect.bisect_right(ts, t) - 1
    return eq[min(max(i, 0), len(eq) - 1)]


def seg_ret(ts, eq, t0, t1):
    """Rendement (fraction) entre t0 et t1, lu sur la courbe d'équité."""
    return eq_at(ts, eq, t1) / eq_at(ts, eq, t0) - 1.0


def d(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def load_runs(strat):
    runs = [(c, *load_run(strat, c)) for c in strat.combos()]   # (combo, ts, eq, trades)
    t0 = min(r[1][0] for r in runs)
    t1 = max(r[1][-1] for r in runs)
    return runs, t0, t1


def ref_run(runs, ref):
    return next(r for r in runs if all(r[0][k] == ref[k] for k in ref))


def pick_champion(runs, t0, t1, min_trades):
    """Meilleur rendement sur [t0,t1] parmi les combos ayant >= min_trades trades."""
    eligibles = [r for r in runs if r[3] >= min_trades]
    if not eligibles:
        return None
    return max(eligibles, key=lambda r: seg_ret(r[1], r[2], t0, t1))


# ── Mode 1 : coupe IS/OOS unique + double heatmap ────────────────────────────
def draw_heat(ax, M, ys, xs, yp, xp, title, champ, ref, vlim):
    im = ax.imshow(M, cmap="RdYlGn", aspect="auto", vmin=-vlim, vmax=vlim, origin="lower")
    ax.set_xticks(range(len(xs))); ax.set_xticklabels(xs)
    ax.set_yticks(range(len(ys))); ax.set_yticklabels(ys)
    ax.set_xlabel(f"{xp} →"); ax.set_ylabel(f"{yp} ↑")
    for i in range(len(ys)):
        for j in range(len(xs)):
            if not np.isnan(M[i, j]):
                ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center", fontsize=7, color="black")
    ax.add_patch(plt.Rectangle((champ[1] - .5, champ[0] - .5), 1, 1, fill=False, ec=ENCRE, lw=2.6))
    ax.add_patch(plt.Rectangle((ref[1] - .5, ref[0] - .5), 1, 1, fill=False, ec=BLEU, lw=2.2, ls="--"))
    ax.set_title(title, fontsize=11, weight="bold", color=ENCRE)
    return im


def run_split(strat, split_frac, min_trades):
    p1, p2 = list(strat.param_grid)
    runs, t0, t1 = load_runs(strat)
    ts_split = t0 + split_frac * (t1 - t0)
    ys = sorted({r[0][p1] for r in runs})
    xs = sorted({r[0][p2] for r in runs})
    Mis = np.full((len(ys), len(xs)), np.nan)
    Moos = np.full((len(ys), len(xs)), np.nan)
    for c, ts, eq, _ in runs:
        i, j = ys.index(c[p1]), xs.index(c[p2])
        Mis[i, j] = seg_ret(ts, eq, t0, ts_split) * 100
        Moos[i, j] = seg_ret(ts, eq, ts_split, t1) * 100

    champ_run = pick_champion(runs, t0, ts_split, min_trades)     # choisi sur l'IS
    if champ_run is None:
        raise SystemExit(f"aucun combo avec >= {min_trades} trades.")
    cc = champ_run[0]; ci = (ys.index(cc[p1]), xs.index(cc[p2]))
    ref = strat.default; ri = (ys.index(ref[p1]), xs.index(ref[p2]))

    print(f"\n=== IS/OOS  {strat.name}  (coupe {split_frac:.0%}, min-trades={min_trades}) ===")
    print(f"IS  = {d(t0)} → {d(ts_split)}   |   OOS = {d(ts_split)} → {d(t1)}\n")
    print(f"  {'combo':24} {'IS':>8} {'OOS':>8}  trades")
    print(f"  champion IS ({cc[p1]},{cc[p2]})".ljust(26) +
          f"{Mis[ci]:>+7.1f}% {Moos[ci]:>+7.1f}%  {champ_run[3]:>5}   <- choisi sur l'IS")
    print(f"  référence   ({ref[p1]},{ref[p2]})".ljust(26) +
          f"{Mis[ri]:>+7.1f}% {Moos[ri]:>+7.1f}%  {ref_run(runs, ref)[3]:>5}   <- formation, PAS optimisé")
    if champ_run[3] == 0:
        print("  [!] champion DÉGÉNÉRÉ (0 trade) — relance avec --min-trades 6 pour une vraie démo.")
    verdict = ("le champion IS TIENT en OOS (bat la référence)" if Moos[ci] >= Moos[ri]
               else "le champion IS s'EFFONDRE en OOS, battu par la référence -> sur-apprentissage")
    print(f"\n  Verdict : {verdict}.")

    vlim = float(np.nanmax(np.abs(np.concatenate([Mis, Moos])))) or 1.0
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(14, 6))
    fig.subplots_adjust(left=0.06, right=0.99, top=0.86, bottom=0.11, wspace=0.22)
    draw_heat(axL, Mis, ys, xs, p1, p2, f"IN-SAMPLE ({d(t0)} → {d(ts_split)})", ci, ri, vlim)
    im = draw_heat(axR, Moos, ys, xs, p1, p2, f"OUT-OF-SAMPLE ({d(ts_split)} → {d(t1)})", ci, ri, vlim)
    fig.colorbar(im, ax=axR, fraction=0.046, pad=0.04, label="rendement (%)")
    fig.suptitle(f"{strat.name.upper()} — le « meilleur » IS (□ noir) reste-t-il vert en OOS ? "
                 f"référence formation = □ bleu", fontsize=12.5, weight="bold", color=ENCRE)
    out = RESULTS_DIR / f"{strat.name}_is_oos.png"
    fig.savefig(out, dpi=150); plt.close(fig)
    print(f"  figure -> {out}")


# ── Mode 2 : walk-forward ancré ──────────────────────────────────────────────
def run_folds(strat, K, min_trades):
    p1, p2 = list(strat.param_grid)
    runs, t0, t1 = load_runs(strat)
    edges = [t0 + k * (t1 - t0) / K for k in range(K + 1)]
    rc = ref_run(runs, strat.default)

    print(f"\n=== WALK-FORWARD ancré  {strat.name}  ({K} folds, min-trades={min_trades}) ===\n")
    print(f"  {'test OOS (fold)':27} {'champion (opt. sur passé)':26} {'OOS champ':>9} {'OOS réf':>8}")
    champ_oos, ref_oos, picks = [], [], []
    for i in range(1, K):
        is_a, is_b = t0, edges[i]                 # ancré : tout le passé connu
        oos_a, oos_b = edges[i], edges[i + 1]
        best = pick_champion(runs, is_a, is_b, min_trades)
        cr = seg_ret(best[1], best[2], oos_a, oos_b)
        rr = seg_ret(rc[1], rc[2], oos_a, oos_b)
        champ_oos.append(cr); ref_oos.append(rr); picks.append(best[0])
        print(f"  {d(oos_a)+' → '+d(oos_b):27} "
              f"{'('+str(best[0][p1])+','+str(best[0][p2])+')':26} "
              f"{cr*100:>+8.1f}% {rr*100:>+7.1f}%")

    champ_tot = (np.prod([1 + r for r in champ_oos]) - 1) * 100
    ref_tot = (np.prod([1 + r for r in ref_oos]) - 1) * 100
    n_uniq = len({combo_id(c) for c in picks})
    print(f"\n  Rendement OOS enchaîné — walk-forward : {champ_tot:+.1f}%   |   "
          f"référence fixe : {ref_tot:+.1f}%")
    print(f"  Stabilité : {n_uniq} réglage(s) différent(s) sur {len(picks)} pas "
          f"({'instable -> peu de régularité à capturer' if n_uniq > len(picks) // 2 else 'plutôt stable'}).")

    x = np.arange(len(champ_oos)); w = 0.38
    fig, ax = plt.subplots(figsize=(12, 5.6))
    fig.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.16)
    ax.bar(x - w/2, [r*100 for r in champ_oos], w, color="#2e7d32", label="walk-forward (ré-optimisé)")
    ax.bar(x + w/2, [r*100 for r in ref_oos], w, color="#607d8b", label="référence fixe (formation)")
    ax.axhline(0, color=ENCRE, lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"fold {i+1}\n{d(edges[i+1])}" for i in range(len(champ_oos))], fontsize=8)
    ax.set_ylabel("rendement OOS du fold (%)")
    ax.set_title(f"{strat.name.upper()} — walk-forward ancré ({K} folds) : "
                 f"ré-optimisé {champ_tot:+.1f}% vs référence fixe {ref_tot:+.1f}% (OOS enchaîné)",
                 fontsize=11.5, weight="bold", color=ENCRE)
    ax.legend(fontsize=9.5); ax.grid(alpha=0.25, axis="y")
    out = RESULTS_DIR / f"{strat.name}_walkforward_{K}.png"
    fig.savefig(out, dpi=150); plt.close(fig)
    print(f"  figure -> {out}")


def main():
    ap = argparse.ArgumentParser(description="IS/OOS et walk-forward, sans re-run LEAN.")
    ap.add_argument("strategy", nargs="?", default="sma", choices=list(STRATEGIES))
    ap.add_argument("--split", type=float, default=0.5, help="fraction IS (coupe unique), défaut 0.5")
    ap.add_argument("--folds", type=int, help="active le walk-forward ancré à K folds")
    ap.add_argument("--min-trades", type=int, default=0, dest="min_trades",
                    help="n'optimise que parmi les combos ayant fait >= N trades")
    args = ap.parse_args()

    strat = STRATEGIES[args.strategy]
    if strat.default is None:
        raise SystemExit(f"{strat.name} n'a pas de combo 'default' (référence) défini.")
    if args.folds:
        run_folds(strat, args.folds, args.min_trades)
    else:
        run_split(strat, args.split, args.min_trades)


if __name__ == "__main__":
    main()
