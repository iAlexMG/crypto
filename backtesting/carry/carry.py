#!/usr/bin/env python3
"""Backtest du carry funding/basis BTCUSDT — delta-neutre (long spot + short perp).

Sur un perpétuel, le funding rattache le prix au spot : quand il est positif (85 %
du temps sur BTC), les longs paient les shorts. Une position NEUTRE (long spot +
short perp, mêmes quantités BTC -> delta ~0 par construction) encaisse donc ce
funding sans pari directionnel. Rendement d'un pas 8h si déployé :

    r = funding(T+8h)  -  (basis(T+8h) - basis(T)) / spot(T)         basis = perp - spot

Le mouvement de prix s'annule entre les deux jambes ; il reste le carry (funding)
moins la convergence du basis. Décision causale : déploiement au pas k décidé avec
le DERNIER funding réalisé (f_k), jamais le futur.

Règles :
  R0 statique      déployé en continu (1 entrée + 1 sortie sur toute la période)
  R2 hystérésis    sort après n_exit funding<=0 consécutifs, rentre après n_enter >0
                   (params choisis IN-SAMPLE, verdict OUT-OF-SAMPLE)
  R1 naïf (témoin) déployé ssi f_k>0 — churne et meurt des frais
Témoins : buy&hold spot ; cash au taux sans risque.

Le résultat honnête est dans le RENDEMENT et la neutralité, PAS le Sharpe : la
couverture parfaite + l'échantillonnage 8h écrasent la volatilité (voir le panneau
RISQUE). Env conda `backtesting`.
"""
from __future__ import annotations

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
FIG_DEFAULT = os.path.normpath(os.path.join(HERE, "..", "site-content", "assets", "figures"))
PPY = 3 * 365                          # pas 8h / an
FEE_TAKER = (5.0 + 10.0) / 1e4         # perp 5 + spot 10 bps, une transition (2 jambes 1 sens)
FEE_MAKER = (2.0 + 10.0) / 1e4
RF_ANN = 0.04                          # taux sans risque sur le cash oisif
RF_STEP = (1 + RF_ANN) ** (1 / PPY) - 1
BASIS_CLIP = 100.0                     # bps — winsorise 2 prints aberrants du perp jeune (2019-20)
OFW = ("2025-08-01", "2026-08-01")     # fenêtre order-flow, pour comparaison
H8_MS = 8 * 3600 * 1000                # 8h en ms — grille de funding


def load() -> pd.DataFrame:
    fund = pd.read_csv(os.path.join(DATA_DIR, "funding.csv"))
    spot = pd.read_csv(os.path.join(DATA_DIR, "klines_spot_8h.csv"))
    perp = pd.read_csv(os.path.join(DATA_DIR, "klines_perp_8h.csv"))
    # Les timestamps de funding jittent de qq ms (jusqu'à ~47 ms) et dérivent avec le temps ;
    # les klines sont pile sur la grille 8h. Planchonner le funding sur la grille 8h AVANT la
    # jointure — sinon un merge sur l'entier exact jette ~43 % des périodes (celles décalées),
    # de façon BIAISÉE (les années à gros funding dérivent plus) -> carry surestimé.
    fund["t"] = (fund["fundingTime"] // H8_MS) * H8_MS
    f = fund[["t", "fundingRate"]].rename(columns={"fundingRate": "f"})
    s = spot[["openTime", "open"]].rename(columns={"openTime": "t", "open": "spot"})
    p = perp[["openTime", "open"]].rename(columns={"openTime": "t", "open": "perp"})
    df = f.merge(s, on="t").merge(p, on="t").sort_values("t").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    raw = (df["perp"] - df["spot"]) / df["spot"] * 1e4
    df["basis"] = raw.clip(-BASIS_CLIP, BASIS_CLIP) / 1e4 * df["spot"]
    df["basis_raw"] = raw / 1e4 * df["spot"]
    df["basis_bps"] = raw.clip(-BASIS_CLIP, BASIS_CLIP)
    df["f_next"] = df["f"].shift(-1)
    df["carry_ret"] = df["f_next"] - (df["basis"].shift(-1) - df["basis"]) / df["spot"]
    df["carry_ret_raw"] = df["f_next"] - (df["basis_raw"].shift(-1) - df["basis_raw"]) / df["spot"]
    df["spot_ret"] = df["spot"].shift(-1) / df["spot"] - 1.0
    return df.iloc[:-1].reset_index(drop=True)


def equity(rets: np.ndarray) -> np.ndarray:
    return np.cumprod(1.0 + np.nan_to_num(rets))


def stats(rets: np.ndarray, states: np.ndarray | None = None) -> dict:
    rets = np.nan_to_num(rets)
    eq = equity(rets)
    n = len(rets)
    vol = rets.std() * np.sqrt(PPY)
    out = {"ann": eq[-1] ** (PPY / n) - 1.0, "total": eq[-1] - 1.0,
           "sharpe": (rets.mean() * PPY) / vol if vol > 0 else 0.0,
           "maxdd": (eq / np.maximum.accumulate(eq) - 1.0).min(), "vol": vol, "eq": eq}
    if states is not None:
        trans = int((states != np.concatenate([[0], states[:-1]])).sum())
        out.update(deployed=states.mean() * 100, trans=trans, fees=trans * FEE_TAKER * 100)
    return out


def simulate(df: pd.DataFrame, states: np.ndarray, fee: float = FEE_TAKER) -> np.ndarray:
    carry = np.nan_to_num(df["carry_ret"].values)
    rets = np.where(states == 1, carry, RF_STEP)
    trans = states != np.concatenate([[0], states[:-1]])
    return rets - trans * fee


def hysteresis(f: np.ndarray, n_enter: int, n_exit: int) -> np.ndarray:
    state = pos = neg = 0
    out = np.zeros(len(f), dtype=int)
    for k, fk in enumerate(f):
        if fk > 0:
            pos, neg = pos + 1, 0
        else:
            neg, pos = neg + 1, 0
        if state == 0 and pos >= n_enter:
            state = 1
        elif state == 1 and neg >= n_exit:
            state = 0
        out[k] = state
    return out


def select_hysteresis(df: pd.DataFrame, f: np.ndarray, half: int) -> tuple[int, int]:
    """Meilleurs (n_enter, n_exit) au sens du Sharpe IN-SAMPLE (1re moitié)."""
    best, best_sh = (9, 9), -1e9
    for ne in (1, 2, 3, 6, 9):
        for nx in (1, 2, 3, 6, 9):
            stt = hysteresis(f, ne, nx)
            sh = stats(simulate(df.iloc[:half], stt[:half]), stt[:half])["sharpe"]
            if sh > best_sh:
                best_sh, best = sh, (ne, nx)
    return best


def fig_equite(df, s0, s1, s2, sbh, ne, nx, half, path):
    fig, ax = plt.subplots(figsize=(11, 6))
    x = df["dt"].values
    ax.plot(x, s0["eq"], label=f"R0 statique ({s0['ann']*100:.1f}%/an, DD {s0['maxdd']*100:.0f}%)", lw=1.9)
    ax.plot(x, s2["eq"], label=f"R2 hystérésis {ne}e/{nx}s ({s2['ann']*100:.1f}%/an) — ≈ statique", lw=1.4, ls="--")
    ax.plot(x, s1["eq"], label=f"R1 naïf f>0 ({s1['ann']*100:.1f}%/an) — meurt du churn", lw=1.0, alpha=0.6)
    ax.plot(x, sbh["eq"], label=f"buy & hold spot ({sbh['ann']*100:.1f}%/an, DD {sbh['maxdd']*100:.0f}%)", lw=1.0, alpha=0.5)
    ax.axvline(pd.Timestamp(df["dt"].iloc[half]), color="k", ls=":", lw=0.8, alpha=0.5)
    ax.text(pd.Timestamp(df["dt"].iloc[half]), ax.get_ylim()[0], " in-sample | out-of-sample", fontsize=8, va="bottom")
    ax.set_yscale("log"); ax.set_ylabel("équité (base 1, échelle log)")
    ax.set_title("Carry funding delta-neutre BTCUSDT — net de frais, sur 7 ans")
    ax.legend(loc="upper left", fontsize=9); ax.grid(True, which="both", alpha=0.25)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def fig_regime(df, path):
    """Carry annualisé par année civile + % de funding positif : la structure et sa dépendance au régime."""
    df = df.copy()
    df["year"] = df["dt"].dt.year
    years, anns, poss = [], [], []
    for y, g in df.groupby("year"):
        years.append(y)
        anns.append(equity(np.nan_to_num(g["carry_ret"].values))[-1] ** (PPY / len(g)) - 1.0)
        poss.append((g["f"].values > 0).mean() * 100)
    fig, ax = plt.subplots(figsize=(11, 5))
    colors = ["#17A2A2" if a >= 0 else "#C0392B" for a in anns]
    bars = ax.bar([str(y) for y in years], [a * 100 for a in anns], color=colors, alpha=0.85)
    for b, a, pp in zip(bars, anns, poss):
        ax.text(b.get_x() + b.get_width() / 2, a * 100 + 0.6, f"{a*100:.1f}%\n{pp:.0f}% pos.",
                ha="center", va="bottom", fontsize=8.5)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylim(top=max(a * 100 for a in anns) * 1.18)   # marge : l'étiquette de 2021 ne touche pas le titre
    ax.set_ylabel("carry annualisé (%/an, statique)")
    ax.set_title("Le carry est structurel mais régime-dépendant — funding BTCUSDT positif la plupart du temps")
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def window(df, lo, hi):
    return df[(df["dt"] >= lo) & (df["dt"] < hi)].reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest carry funding delta-neutre.")
    ap.add_argument("--figures", default=FIG_DEFAULT, help="dossier des figures (défaut : site-content assets)")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()

    df = load()
    f, n = df["f"].values, len(df)
    half = n // 2
    print(f"Grille : {df['dt'].iloc[0]:%Y-%m-%d} -> {df['dt'].iloc[-1]:%Y-%m-%d}  ({n} pas 8h, {n/PPY:.1f} ans)")
    print(f"Hypothèses : frais taker {FEE_TAKER*1e4:.0f} bps/transition, rf {RF_ANN*100:.0f}%/an, "
          f"basis winsorisé ±{BASIS_CLIP:.0f} bps, marge unifiée.\n")

    st_static, st_naive = np.ones(n, int), (f > 0).astype(int)
    s0 = stats(simulate(df, st_static), st_static)
    s1 = stats(simulate(df, st_naive), st_naive)
    sbh = stats(df["spot_ret"].values)
    scash = stats(np.full(n, RF_STEP))
    ne, nx = select_hysteresis(df, f, half)
    st_hys = hysteresis(f, ne, nx)
    s2 = stats(simulate(df, st_hys), st_hys)

    def line(name, s, extra=""):
        print(f"{name:<28}{s['ann']*100:>7.2f}{s['total']*100:>9.1f}{s['sharpe']:>8.2f}{s['maxdd']*100:>8.1f}  {extra}")
    print(f"{'stratégie (net, 7 ans)':<28}{'ann%':>7}{'total%':>9}{'Sharpe':>8}{'maxDD%':>8}")
    line("R0 statique neutre", s0, f"({s0['trans']} transitions)")
    line(f"R2 hystérésis {ne}e/{nx}s", s2, f"déployé {s2['deployed']:.0f}%, {s2['trans']} trans")
    line("R1 naïf f>0 (témoin churn)", s1, f"{s1['trans']} trans, {s1['fees']:.0f}% en frais")
    line("BH buy & hold spot", sbh)
    line(f"CASH rf {RF_ANN*100:.0f}%/an", scash)

    carry_raw = np.nan_to_num(df["carry_ret_raw"].values)
    r0_raw = carry_raw.copy(); r0_raw[0] -= FEE_TAKER; r0_raw[-1] -= FEE_TAKER
    s0r = stats(r0_raw, st_static)
    print(f"\nRISQUE (R0) — la couverture parfaite écrase la vol, le Sharpe n'est PAS réel :")
    print(f"  basis brut      : vol {s0r['vol']*100:5.2f}%/an  Sharpe {s0r['sharpe']:5.2f}  maxDD {s0r['maxdd']*100:5.1f}%")
    print(f"  basis winsorisé : vol {s0['vol']*100:5.2f}%/an  Sharpe {s0['sharpe']:5.2f}  maxDD {s0['maxdd']*100:5.1f}%")
    print(f"  -> vrai risque (gaps de basis intra-8h, marge/liquidation, tracking error) hors de ces données 8h.")

    s2m = stats(simulate(df, st_hys, fee=FEE_MAKER), st_hys)
    print(f"\nVariantes : R2 maker -> {s2m['ann']*100:+.2f}% (vs {s2['ann']*100:+.2f}% taker) ; "
          f"marge isolée 5x -> ×1/1.2 (R0 {s0['ann']*100:.1f} -> {s0['ann']*100/1.2:.1f}%/an).")

    print(f"\nHors-échantillon (hystérésis {ne}e/{nx}s fixée sur la 1re moitié) :")
    for tag, sl in (("IS  1re moitié", slice(0, half)), ("OOS 2e moitié", slice(half, None))):
        g = df.iloc[sl].reset_index(drop=True)
        stt = hysteresis(g["f"].values, ne, nx)
        a0, a2 = stats(simulate(g, np.ones(len(g), int))), stats(simulate(g, stt), stt)
        print(f"  {tag} ({g['dt'].iloc[0]:%Y-%m}->{g['dt'].iloc[-1]:%Y-%m}) : "
              f"R0 {a0['ann']*100:+6.2f}% DD {a0['maxdd']*100:5.1f}%  |  R2 {a2['ann']*100:+6.2f}% DD {a2['maxdd']*100:5.1f}%")

    w = window(df, *OFW)
    stw = hysteresis(w["f"].values, ne, nx)
    print(f"\nFenêtre order-flow {OFW[0]}->{OFW[1]} : "
          f"R0 {stats(simulate(w, np.ones(len(w), int)))['ann']*100:+.2f}%  "
          f"R2 {stats(simulate(w, stw), stw)['ann']*100:+.2f}%")

    if not args.no_figures:
        os.makedirs(args.figures, exist_ok=True)
        fig_equite(df, s0, s1, s2, sbh, ne, nx, half, os.path.join(args.figures, "carry-equite.png"))
        fig_regime(df, os.path.join(args.figures, "carry-regime.png"))
        print(f"\n-> figures dans {args.figures} : carry-equite.png, carry-regime.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
