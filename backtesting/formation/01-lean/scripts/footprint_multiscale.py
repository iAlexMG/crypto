"""Footprint multi-échelles : l'agression acheteur/vendeur PAR NIVEAU, à plusieurs résolutions.

Même idée que vp_multiscale.py (mêmes briques de ticks, même session), mais la lecture n'est
plus le VOLUME total par niveau — c'est le DELTA (acheteur vs vendeur) par niveau :

  1. sous-footprint_Xm : chaque brique de X min = un footprint. PAR NIVEAU de prix, deux
     barres divergentes : vendeur ←  (rouge)  |  → acheteur (vert). L'asymétrie = le delta.
  2. footprint_fullNY : en marge droite, le profil de DELTA CUMULÉ par niveau de toute la
     session (vert = net acheteur, rouge = net vendeur) — OÙ l'agression nette s'est concentrée.
  3. dynamique : le niveau de |delta| dominant GLISSANT (N dernières min, ligne violette)
     suit OÙ l'agression nette récente se concentre. Panneau du bas : delta par brique +
     delta cumulé de session (le compteur d'agression nette).

Conventions communes à vp_multiscale.py (largeur = temps, axe NY, sessions = source unique).

  python backtests/orderflow/footprint_multiscale.py --date 2026-03-10 --tf 5m
  python backtests/orderflow/footprint_multiscale.py --date 2026-03-10 --session london --tf 1m
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

# Réutilise les briques/sessions/échelle de l'outil VP (DRY, mêmes sources uniques)
from vp_multiscale import (DB, FIG_DIR, ENCRE, VERT, ROUGE, NY, parse_tf,
                           session_bounds, stream_bricks, merge_profiles)

DELTA_DOM = "#7c3aed"     # niveau de |delta cumulé| dominant (migration)
DELTA_POC = "#0b2545"     # delta-POC de session (footprint_fullNY)


def delta_by_level(prof: dict) -> dict:
    """{niveau: achat - vente} pour un profil."""
    return {lvl: a - v for lvl, (a, v) in prof.items()}


def rolling_dominant_level(bricks, window_bricks: int):
    """Niveau de |delta| MAX sur les `window_bricks` dernières briques : où l'agression
    nette RÉCENTE se concentre (migre avec l'action, au lieu de parquer sur un extrême)."""
    from collections import deque
    win: deque = deque(maxlen=window_bricks)
    out = []
    for ts, prof, _ in bricks:
        win.append(prof)
        run: dict = {}
        for p in win:
            for lvl, (a, v) in p.items():
                c = run.setdefault(lvl, [0.0, 0.0])
                c[0] += a; c[1] += v
        lvl = max(run, key=lambda l: abs(run[l][0] - run[l][1]))
        out.append((ts, lvl, run[lvl][0] - run[lvl][1]))
    return out


def build_figure(bricks, full, dev, t0, t1, brick_ms, tick, meta, out_path):
    brick_days = brick_ms / 1000 / 86400
    full_delta = delta_by_level(full)
    dpoc = max(full_delta, key=lambda l: abs(full_delta[l]))     # delta-POC de session

    fig = plt.figure(figsize=(15, 8.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[5, 1], height_ratios=[3.3, 1],
                          wspace=0.015, hspace=0.05)
    ax = fig.add_subplot(gs[0, 0])                      # footprint prix × temps
    axm = fig.add_subplot(gs[0, 1], sharey=ax)          # footprint_fullNY (delta cumulé)
    axd = fig.add_subplot(gs[1, 0], sharex=ax)          # delta par brique + cumulé
    fig.subplots_adjust(left=0.055, right=0.955, top=0.9, bottom=0.09)

    # --- panneau principal : un footprint (delta divergent par niveau) par brique ---
    tprix, close = [], []
    delta_brique = []
    for tsb, prof, c in bricks:
        center = mdates.date2num(datetime.fromtimestamp(tsb / 1000, tz=timezone.utc)) \
            + brick_days / 2
        niveaux = np.array(sorted(prof))
        achat = np.array([prof[l][0] for l in niveaux])
        vente = np.array([prof[l][1] for l in niveaux])
        m = max(achat.max(), vente.max()) or 1.0
        half = brick_days / 2 * 0.9
        lv, la = vente / m * half, achat / m * half           # longueurs vendeur / acheteur
        ax.barh(niveaux, -lv, left=center, height=tick, color=ROUGE, alpha=0.9)   # vendeur ←
        ax.barh(niveaux, la, left=center, height=tick, color=VERT, alpha=0.9)     # → acheteur
        tprix.append(mdates.date2num(datetime.fromtimestamp((tsb + brick_ms) / 1000, tz=timezone.utc)))
        close.append(c)
        delta_brique.append((center, achat.sum() - vente.sum()))

    # --- delta-POC de session (référence) + migration du niveau dominant ---
    xa = mdates.date2num(datetime.fromtimestamp(t0 / 1000, tz=timezone.utc))
    xb = mdates.date2num(datetime.fromtimestamp(t1 / 1000, tz=timezone.utc))
    ax.hlines(dpoc, xa, xb, color=DELTA_POC, lw=1.8,
              label=f"delta-POC session ({dpoc:,.0f}, Δ={full_delta[dpoc]:+,.0f})")
    xdev = [mdates.date2num(datetime.fromtimestamp((ts + brick_ms) / 1000, tz=timezone.utc))
            for ts, *_ in dev]
    ax.plot(xdev, [l for _, l, _ in dev], color=DELTA_DOM, lw=1.6,
            label=f"niveau |Δ| dominant glissant ({meta['window']})")
    ax.plot(tprix, close, color="#111", lw=1.2, marker=".", ms=3, alpha=0.85,
            label=f"Prix (close {meta['tf']})")
    ax.barh(np.nan, 0, color=VERT, label="acheteur (agresseur)")
    ax.barh(np.nan, 0, color=ROUGE, label="vendeur (agresseur)")
    ax.set_ylabel("Prix ($)")
    ax.set_xlim(xa, xb)
    ax.legend(loc="lower left", fontsize=8.2, framealpha=0.92, ncol=2)
    ax.set_title(f"Footprint {meta['tf']} sur prix × temps — par niveau : vendeur ← | → acheteur",
                 fontsize=12, weight="bold", color=ENCRE)

    # --- marge : footprint_fullNY = delta CUMULÉ par niveau (net acheteur/vendeur) ---
    niv = np.array(sorted(full_delta))
    dd = np.array([full_delta[l] for l in niv])
    axm.barh(niv, dd, height=tick, color=np.where(dd >= 0, VERT, ROUGE), alpha=0.85)
    axm.axvline(0, color=ENCRE, lw=0.8)
    axm.axhline(dpoc, color=DELTA_POC, lw=1.8)
    axm.set_title("footprint_fullNY\n(Δ cumulé/niveau)", fontsize=9.5, weight="bold", color=ENCRE)
    axm.tick_params(axis="y", labelleft=False)
    axm.tick_params(axis="x", labelsize=7)
    axm.set_xlabel("Δ (BTC)", fontsize=8)
    axm.grid(alpha=0.15)

    # --- bas : delta par brique (barres) + delta CUMULÉ de session (ligne) ---
    xc = [c for c, _ in delta_brique]
    db = [d for _, d in delta_brique]
    axd.bar(xc, db, width=brick_days * 0.85, color=np.where(np.array(db) >= 0, VERT, ROUGE),
            alpha=0.75)
    axd.axhline(0, color=ENCRE, lw=0.8)
    axd.set_ylabel(f"Δ / {meta['tf']}\n(BTC)", fontsize=8.5)
    axcum = axd.twinx()
    axcum.plot(xc, np.cumsum(db), color=DELTA_DOM, lw=1.8)
    axcum.set_ylabel("Δ cumulé\nsession", fontsize=8, color=DELTA_DOM)
    axcum.tick_params(axis="y", labelcolor=DELTA_DOM, labelsize=7)

    # --- axe temps en heure de New York ---
    def _fmt(x, pos=None):
        dt = mdates.num2date(x, tz=NY)
        return dt.strftime("%H:%M\n%d %b") if (dt.hour == 0 or pos == 0) else dt.strftime("%H:%M")

    for a in (ax, axd):
        a.xaxis.set_major_locator(mdates.HourLocator(tz=NY))
        a.grid(which="major", axis="x", color=ENCRE, alpha=0.18, lw=0.6)
        a.grid(which="major", axis="y", alpha=0.18)
    ax.tick_params(axis="x", labelbottom=False)
    axd.xaxis.set_major_formatter(FuncFormatter(_fmt))
    axd.set_xlabel("heure de New York", fontsize=9, color=ENCRE)

    net = sum(a - v for a, v in full.values())
    fig.suptitle(f"{meta['session'].upper()} {meta['date']} ({meta['plage']}) — "
                 f"footprint {meta['tf']} vs footprint_fullNY  ·  Δ session {net:+,.0f} BTC  "
                 f"·  delta-POC {dpoc:,.0f}", fontsize=12.5, weight="bold", color=ENCRE)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="Footprint multi-échelles (analyse order-flow).")
    ap.add_argument("--date", default="2026-03-10", help="jour NY, ex. 2026-03-10")
    ap.add_argument("--session", default="ny", choices=["asia", "london", "ny"])
    ap.add_argument("--tf", default="5m", help="taille des sous-footprints : 1m, 5m, 15m…")
    ap.add_argument("--window", default="60m", help="fenêtre du niveau |Δ| dominant, ex. 60m")
    ap.add_argument("--tick", type=float, default=25.0, help="taille d'un niveau de prix ($)")
    args = ap.parse_args()

    brick_ms = parse_tf(args.tf)
    window_bricks = max(1, round(parse_tf(args.window) / brick_ms))
    t0, t1, plage = session_bounds(args.date, args.session)
    print(f"[{args.session} {args.date}] {plage}  |  footprint {args.tf}, "
          f"niveau dominant glissant {args.window} ({window_bricks} briques)")
    bricks = stream_bricks(DB, t0, t1, brick_ms, args.tick)
    if not bricks:
        raise SystemExit("aucun tick sur cette fenêtre.")
    full = merge_profiles(bricks)
    dev = rolling_dominant_level(bricks, window_bricks)
    net = sum(a - v for a, v in full.values())
    fd = delta_by_level(full)
    dpoc = max(fd, key=lambda l: abs(fd[l]))
    print(f"  {len(bricks)} briques · Δ session {net:+,.0f} BTC · delta-POC {dpoc:,.0f} "
          f"(Δ={fd[dpoc]:+,.0f})")

    meta = {"date": args.date, "session": args.session, "tf": args.tf, "plage": plage,
            "window": args.window}
    out = FIG_DIR / f"lecon-09-footprint-{args.session}-{args.tf}.png"
    build_figure(bricks, full, dev, t0, t1, brick_ms, args.tick, meta, out)
    print(f"  figure -> {out}")


if __name__ == "__main__":
    main()
