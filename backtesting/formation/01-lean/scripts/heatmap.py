"""Heatmaps 2D et 3D d'un balayage de paramètres (lit le CSV de run_grid.py).

Trois sorties dans backtests/optimize/results/ pour la métrique choisie :
  <strat>_heatmap_<metric>.png     — heatmap 2D annotée (style figures de la formation)
  <strat>_surface3d_<metric>.png   — surface 3D statique (matplotlib, sans dépendance)
  <strat>_surface3d_<metric>.html  — surface 3D INTERACTIVE (plotly) : tu la fais
                                     tourner/zoomes à la souris, survol = valeurs exactes

Le meilleur combo est encadré (2D) et marqué d'une boule (3D). Les combinaisons
invalides (rapide ≥ lente) restent des trous — la zone valide est triangulaire.

Exemples
    python backtests/optimize/heatmap.py sma                 # métrique = sharpe
    python backtests/optimize/heatmap.py sma --metric net_profit --open
"""
from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

RESULTS_DIR = Path(__file__).resolve().parent / "results"
ENCRE = "#263238"

# Métrique -> (libellé lisible, "plus haut = mieux" ?)
METRIC_INFO = {
    "sharpe":            ("Sharpe Ratio", True),
    "sortino":           ("Sortino Ratio", True),
    "net_profit":        ("Net Profit (%)", True),
    "car":               ("CAR annualisé (%)", True),
    "psr":               ("Probabilistic Sharpe (%)", True),
    "win_rate":          ("Win Rate (%)", True),
    "profit_loss_ratio": ("Ratio gain/perte", True),
    "drawdown":          ("Drawdown max (%)", False),
    "total_fees":        ("Frais totaux ($)", False),
}
# colonnes de métriques (le reste des colonnes = les paramètres balayés)
NON_PARAM = set(METRIC_INFO) | {"loss_rate", "total_orders", "status"}


def load_grid(strat: str):
    csv_path = RESULTS_DIR / f"{strat}_grid.csv"
    if not csv_path.exists():
        raise SystemExit(f"{csv_path} introuvable — lance d'abord run_grid.py {strat}")
    df = pd.read_csv(csv_path)
    params = [c for c in df.columns if c not in NON_PARAM]
    if len(params) != 2:
        raise SystemExit(f"heatmap 2D/3D : il faut exactement 2 paramètres, trouvé {params}")
    return df, params[0], params[1]   # (df, y_param=rapide, x_param=lente)


def build_matrix(df, yp, xp, metric):
    ys = sorted(df[yp].unique())
    xs = sorted(df[xp].unique())
    M = np.full((len(ys), len(xs)), np.nan)
    for _, r in df.iterrows():
        if r.get("status") == "FAILED":
            continue
        M[ys.index(r[yp]), xs.index(r[xp])] = r[metric]
    return M, ys, xs


def best_cell(M, higher_better):
    flat = np.nanargmax(M) if higher_better else np.nanargmin(M)
    return np.unravel_index(flat, M.shape)   # (i_y, j_x)


def plot_2d(M, ys, xs, yp, xp, metric, label, higher, out):
    bi, bj = best_cell(M, higher)
    vmax = np.nanmax(np.abs(M)); vmin = -vmax                    # divergent centré sur 0
    fig, ax = plt.subplots(figsize=(9.5, 6.2))
    fig.subplots_adjust(left=0.09, right=1.02, top=0.9, bottom=0.11)
    im = ax.imshow(M, cmap="RdYlGn", aspect="auto", vmin=vmin, vmax=vmax, origin="lower")
    ax.set_xticks(range(len(xs))); ax.set_xticklabels(xs)
    ax.set_yticks(range(len(ys))); ax.set_yticklabels(ys)
    ax.set_xlabel(f"{xp} →"); ax.set_ylabel(f"{yp} ↑")
    for i in range(len(ys)):
        for j in range(len(xs)):
            if not np.isnan(M[i, j]):
                ax.text(j, i, f"{M[i, j]:.2f}", ha="center", va="center",
                        fontsize=7.5, color="black")
    ax.add_patch(plt.Rectangle((bj - .5, bi - .5), 1, 1, fill=False, ec=ENCRE, lw=2.6))
    ax.set_title(f"Heatmap {label} — meilleur = ({ys[bi]},{xs[bj]}) à {M[bi, bj]:+.3f}",
                 fontsize=12, weight="bold", color=ENCRE)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=label)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return (ys[bi], xs[bj], M[bi, bj])


def plot_3d_static(M, ys, xs, yp, xp, label, higher, out):
    bi, bj = best_cell(M, higher)
    X, Y = np.meshgrid(range(len(xs)), range(len(ys)))
    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_surface(X, Y, M, cmap="RdYlGn", edgecolor="#455a64", linewidth=0.3,
                    antialiased=True, rstride=1, cstride=1)
    ax.scatter([bj], [bi], [M[bi, bj]], color=ENCRE, s=80, depthshade=False)
    ax.text(bj, bi, M[bi, bj], f"  ({ys[bi]},{xs[bj]})", color=ENCRE, fontsize=9, weight="bold")
    ax.set_xticks(range(len(xs))); ax.set_xticklabels(xs, fontsize=8)
    ax.set_yticks(range(len(ys))); ax.set_yticklabels(ys, fontsize=8)
    ax.set_xlabel(xp); ax.set_ylabel(yp); ax.set_zlabel(label)
    ax.set_title(f"Surface 3D — {label}", fontsize=12, weight="bold", color=ENCRE)
    ax.view_init(elev=28, azim=-52)
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_3d_interactive(M, ys, xs, yp, xp, label, higher, out):
    import plotly.graph_objects as go
    bi, bj = best_cell(M, higher)
    fig = go.Figure(go.Surface(
        z=M, x=xs, y=ys, colorscale="RdYlGn", cmid=0,
        colorbar=dict(title=label),
        hovertemplate=f"{yp}=%{{y}}<br>{xp}=%{{x}}<br>{label}=%{{z:.3f}}<extra></extra>",
        contours={"z": {"show": True, "usecolormap": True,
                        "highlightcolor": "#37474f", "project": {"z": True}}},
    ))
    fig.add_trace(go.Scatter3d(
        x=[xs[bj]], y=[ys[bi]], z=[M[bi, bj]], mode="markers+text",
        marker=dict(size=6, color="#111"), text=[f" meilleur ({ys[bi]},{xs[bj]})"],
        textposition="top center", hoverinfo="skip", showlegend=False,
    ))
    fig.update_layout(
        title=f"{label} — surface 3D interactive (fais-la tourner à la souris)",
        scene=dict(xaxis_title=xp, yaxis_title=yp, zaxis_title=label),
        margin=dict(l=0, r=0, t=40, b=0), height=720,
    )
    # plotly.js EMBARQUÉ : le HTML s'ouvre hors-ligne et se déplace/partage tel quel.
    fig.write_html(out, include_plotlyjs=True)


def main():
    ap = argparse.ArgumentParser(description="Heatmaps 2D/3D d'un balayage de paramètres.")
    ap.add_argument("strategy", nargs="?", default="sma")
    ap.add_argument("--metric", default="sharpe", choices=list(METRIC_INFO))
    ap.add_argument("--open", action="store_true", help="ouvre la 3D interactive dans le navigateur")
    args = ap.parse_args()

    label, higher = METRIC_INFO[args.metric]
    df, yp, xp = load_grid(args.strategy)
    M, ys, xs = build_matrix(df, yp, xp, args.metric)

    stem = RESULTS_DIR / f"{args.strategy}_{{}}_{args.metric}"
    png2d = Path(str(stem).format("heatmap") + ".png")
    png3d = Path(str(stem).format("surface3d") + ".png")
    html3d = Path(str(stem).format("surface3d") + ".html")

    by, bx, bz = plot_2d(M, ys, xs, yp, xp, args.metric, label, higher, png2d)
    plot_3d_static(M, ys, xs, yp, xp, label, higher, png3d)
    plot_3d_interactive(M, ys, xs, yp, xp, label, higher, html3d)

    n_valid = int(np.sum(~np.isnan(M)))
    print(f"[{args.strategy}] métrique={args.metric} ({label}), {n_valid} combos tracés")
    print(f"  meilleur : {yp}={by}, {xp}={bx}  ->  {label} = {bz:+.3f}")
    print(f"  2D  -> {png2d}")
    print(f"  3D  -> {png3d}")
    print(f"  3D interactif -> {html3d}")
    if args.open:
        webbrowser.open(html3d.as_uri())


if __name__ == "__main__":
    main()
