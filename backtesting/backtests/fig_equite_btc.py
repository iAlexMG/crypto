"""Figures des fiches BTC : courbe d'équité LEAN de chaque stratégie, avec le
Buy & Hold BTC en référence et la frontière IS/OOS (mi-fenêtre 20 j / 20 j).

Miroir de fig_equite_nq.py (indices) — même style maison, mêmes conventions.
Tout est RELU depuis les JSON de résultats LEAN (charts["Strategy Equity"]),
jamais figé.

    python backtests/fig_equite_btc.py --resultats <dossier des runs> \
           --out <iAlexMG.ca>/assets/crypto/backtesting/figures \
           [--slugs buyhold,sma,...]

Le dossier des runs contient un sous-dossier par stratégie (buyhold,
sma_croisement, …), chacun avec son <Classe>.json ecrit par le Launcher.
Le Buy & Hold sert de reference aux 7 autres : lancer son run en premier.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ENCRE, BLEU, ROUGE, AMBRE, GRIS = "#263238", "#1d4ed8", "#c62828", "#b45309", "#78909c"
SPLIT = datetime(2026, 6, 21, tzinfo=timezone.utc)   # frontiere IS/OOS (mi-fenetre)

# (slug de figure, dossier du run, classe LEAN, titre)
RUNS = [
    ("buyhold", "buyhold", "BuyHold", "Buy & Hold BTC (1 unite)"),
    ("sma", "sma_croisement", "SmaCroisement", "Croisement SMA 9/21 — BTC 1 m"),
    ("macd", "macd", "Macd", "MACD 12/26/9 — BTC 1 m"),
    ("rsi", "rsi_retour_moyenne", "RsiRetourMoyenne", "RSI 9 (30/70) — BTC 1 m"),
    ("bollinger", "bollinger", "Bollinger", "Bandes de Bollinger 20/2σ — BTC 1 m"),
    ("risque", "risque_stops", "RisqueStops", "RSI 9 + stops ATR — BTC 1 m"),
    ("avancee", "strategie_avancee", "StrategieAvancee", "Strategie avancee — BTC 1 m"),
    ("vp", "volume_profile", "VolumeProfile", "Volume profile par session — BTC 1 m"),
]


def equite(dossier: Path, classe: str):
    d = json.load(open(dossier / f"{classe}.json"))
    vals = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
    t = [datetime.fromtimestamp(v[0], tz=timezone.utc) for v in vals]
    return t, [v[4] for v in vals]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resultats", required=True, help="dossier des runs LEAN")
    ap.add_argument("--out", required=True, help="dossier des PNG (assets du site)")
    ap.add_argument("--slugs", default="", help="sous-ensemble (defaut : tous ceux dont le run existe)")
    args = ap.parse_args()
    base, out = Path(args.resultats), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    voulus = set(s.strip() for s in args.slugs.split(",") if s.strip())

    t_bh, eq_bh = equite(base / "buyhold", "BuyHold")   # reference

    for slug, dossier, classe, titre in RUNS:
        if voulus and slug not in voulus:
            continue
        if not (base / dossier / f"{classe}.json").exists():
            print(f"-- {slug} : run absent, ignore")
            continue
        t, eq = equite(base / dossier, classe)
        depart, fin = eq[0], eq[-1]

        fig, ax = plt.subplots(figsize=(11, 5.2))
        if slug != "buyhold":
            ax.plot(t_bh, eq_bh, color=GRIS, lw=1.2, alpha=0.8,
                    label=f"Buy & Hold BTC ({eq_bh[-1] / eq_bh[0] - 1:+.1%})")
        ax.plot(t, eq, color=BLEU, lw=1.6,
                label=f"{titre.split(' — ')[0]} ({fin / depart - 1:+.1%})")
        ax.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.55)
        ax.text(t[2], depart, f"  capital de depart {depart:,.0f} $", fontsize=9.5,
                color=ENCRE, va="bottom")

        ax.axvline(SPLIT, color=AMBRE, lw=1.2, ls=":", alpha=0.9)
        ax.text(SPLIT, ax.get_ylim()[1], " in-sample ◀ ", fontsize=9, color=AMBRE,
                ha="right", va="top", weight="bold")
        ax.text(SPLIT, ax.get_ylim()[1], " ▶ out-of-sample", fontsize=9, color=AMBRE,
                ha="left", va="top", weight="bold")

        couleur_fin = ROUGE if fin < depart else AMBRE
        ax.plot(t[-1], fin, "o", ms=8, mfc=couleur_fin, mec="white", mew=1.4, zorder=6)
        ax.annotate(f"equite finale {fin:,.0f} $ ({fin / depart - 1:+.2%})",
                    xy=(t[-1], fin), xytext=(-215, 30), textcoords="offset points",
                    fontsize=10, weight="bold", color=couleur_fin,
                    arrowprops=dict(arrowstyle="->", lw=1.5, color=couleur_fin,
                                    connectionstyle="arc3,rad=0.25"))

        ax.set_title(f"{titre} — fenetre 2026-06-01 → 2026-07-10",
                     fontsize=12, weight="bold", color=ENCRE)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        ax.set_ylabel("Equite ($)")
        ax.grid(alpha=0.25)
        ax.legend(loc="best", fontsize=9.5)
        fig.tight_layout()
        dst = out / f"equite-{slug}.png"
        fig.savefig(dst, dpi=150, facecolor="white")
        plt.close(fig)
        print(f"OK -> equite-{slug}.png  ({depart:,.0f} -> {fin:,.0f} $)")


if __name__ == "__main__":
    main()
