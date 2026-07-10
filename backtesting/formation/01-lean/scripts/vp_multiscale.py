"""VP multi-échelles : observer l'order-flow d'UNE session à plusieurs résolutions.

Outil d'ANALYSE (pas de backtest). Pour une session (NY par défaut) d'une date donnée,
sur un canvas PRIX × TEMPS, il montre d'un coup d'œil trois choses et leur DYNAMIQUE :

  1. les sous-VP_Xm : la session découpée en briques de X minutes, chaque brique = son
     mini volume-profile (volume par niveau de prix), tracé dans son créneau de X min ;
  2. le VP_fullNY : le profil CUMULÉ de TOUTE la session, en panneau marginal à droite
     (le profil vers lequel les sous-VP s'agrègent) ;
  3. la migration de la VALEUR : le POC GLISSANT (les N dernières minutes) suit la valeur
     EN FORMATION, près du prix — à comparer au POC de session (fixe) du VP_fullNY.

Conventions (mémoire projet, cf. fig_vp_prix_temps.py) :
  - largeur d'une brique = le TEMPS (X min), identique partout ; chaque profil normalisé
    SUR LUI-MÊME (barre du POC ≈ 90 % du créneau) — la largeur n'encode PAS le volume ;
  - couleur = ratio delta/volume du niveau ∈ [−1,+1] (rouge→blanc→vert = intensité
    d'agression, indépendante du volume brut) ;
  - le volume total (perdu par la normalisation) revient en panneau du bas ;
  - axe X en heure de New York ; sessions depuis backtests/sessions.py.

Exemples
    python backtests/orderflow/vp_multiscale.py --date 2026-03-10 --tf 5m
    python backtests/orderflow/vp_multiscale.py --date 2026-03-10 --tf 1m --coverage 0.80
    python backtests/orderflow/vp_multiscale.py --date 2026-03-10 --session london --tf 15m
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.ticker import FuncFormatter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))       # racine du dépôt
from backtests.sessions import NY, NOM, bornes_sessions            # sessions = source unique
from backtests.volume_profile_features import value_area           # value area = source unique

DB = "F:/data/BTCUSDT-um.db"
FIG_DIR = Path(__file__).resolve().parents[1] / "assets" / "images"   # formation/01-lean/assets/images
ENCRE = "#263238"
CMAP = LinearSegmentedColormap.from_list(
    "ratio", ["#b91c1c", "#f2b8b8", "#ffffff", "#b7e2c1", "#15803d"])
VERT, ROUGE = "#16a34a", "#dc2626"
POC_DEV = "#7c3aed"      # POC en développement (migration)
POC_FULL = "#0b2545"     # POC de la session (VP_fullNY)
RSAT = 0.5               # ratio à saturation de couleur (|delta|/vol)


def parse_tf(s: str) -> int:
    """'5m' -> 300000 ms ; '1h' -> 3600000 ms ; '30' -> minutes par défaut."""
    s = s.strip().lower()
    if s.endswith("m"):
        return int(s[:-1]) * 60_000
    if s.endswith("h"):
        return int(s[:-1]) * 3_600_000
    return int(s) * 60_000


def session_bounds(date_str: str, session: str) -> tuple[int, int, str]:
    """(t0_ms, t1_ms, libellé) de la `session` démarrant le jour NY `date_str`."""
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    anchor = int(datetime(day.year, day.month, day.day, tzinfo=NY).timestamp() * 1000)
    bornes = bornes_sessions(anchor - 2 * 86_400_000, anchor + 3 * 86_400_000)
    for i, (ms, nom) in enumerate(bornes):
        if nom == session and datetime.fromtimestamp(ms / 1000, tz=NY).date() == day:
            fin = bornes[i + 1][0]
            plage = (f"{datetime.fromtimestamp(ms/1000, tz=NY):%H:%M} → "
                     f"{datetime.fromtimestamp((fin-60000)/1000, tz=NY):%H:%M} NY")
            return ms, fin, plage
    raise SystemExit(f"session '{session}' introuvable le {date_str} "
                     f"(attendu : asia | london | ny)")


def stream_bricks(db: str, t0: int, t1: int, brick_ms: int, tick: float):
    """Ticks [t0,t1) -> [(ts_brique, {niveau: [achat, vente]}, close)] en briques de X min."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.execute("SELECT ts, price, size, side='buy' FROM trades "
                      "WHERE ts>=? AND ts<? ORDER BY trade_id", (t0, t1))
    bricks, tsb, prof, lastp = [], None, None, None
    for ts, prix, taille, achat in cur:
        b = (ts - t0) // brick_ms * brick_ms + t0            # briques alignées sur l'ouverture
        if b != tsb:
            if tsb is not None:
                bricks.append((tsb, prof, lastp))
            tsb, prof = b, {}
        lvl = round(prix / tick) * tick
        cell = prof.setdefault(lvl, [0.0, 0.0])
        cell[0 if achat else 1] += taille
        lastp = prix
    if tsb is not None:
        bricks.append((tsb, prof, lastp))
    con.close()
    return bricks


def merge_profiles(bricks) -> dict:
    """Somme des mini-profils -> le profil de session complet (VP_fullNY)."""
    full: dict = {}
    for _, prof, _ in bricks:
        for lvl, (a, v) in prof.items():
            c = full.setdefault(lvl, [0.0, 0.0])
            c[0] += a; c[1] += v
    return full


def rolling_va(bricks, window_bricks: int, coverage: float):
    """POC/VAH/VAL du profil GLISSANT (les `window_bricks` dernières briques), brique par
    brique : suit la valeur EN FORMATION (proche du prix), au lieu de coller à l'ouverture."""
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
        poc, vah, val = value_area(run, coverage)
        out.append((ts, poc, vah, val))
    return out


def build_figure(bricks, full, dev, t0, t1, brick_ms, tick, coverage, meta, out_path):
    brick_days = brick_ms / 1000 / 86400
    norm = Normalize(-RSAT, RSAT)
    poc_f, vah_f, val_f = value_area(full, coverage)

    fig = plt.figure(figsize=(15, 8.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[5, 1], height_ratios=[3.3, 1],
                          wspace=0.015, hspace=0.05)
    ax = fig.add_subplot(gs[0, 0])                      # prix × temps (sous-VP_Xm)
    axm = fig.add_subplot(gs[0, 1], sharey=ax)          # VP_fullNY (marge droite)
    axv = fig.add_subplot(gs[1, 0], sharex=ax)          # volume par brique
    fig.subplots_adjust(left=0.055, right=0.9, top=0.9, bottom=0.09)

    # --- panneau principal : un mini-VP par brique de X min ---
    tprix, close = [], []
    for tsb, prof, c in bricks:
        x0 = mdates.date2num(datetime.fromtimestamp(tsb / 1000, tz=timezone.utc))
        niveaux = np.array(sorted(prof))
        achat = np.array([prof[l][0] for l in niveaux])
        vente = np.array([prof[l][1] for l in niveaux])
        tot = achat + vente
        ratio = (achat - vente) / np.where(tot > 0, tot, 1)
        largeur = tot / tot.max() * brick_days * 0.9      # normalisé sur la brique
        ax.barh(niveaux, largeur, left=x0, height=tick, color=CMAP(norm(ratio)),
                edgecolor="none")
        xc = x0 + brick_days / 2
        axv.bar(xc, achat.sum(), width=brick_days * 0.85, color=VERT, alpha=0.8)
        axv.bar(xc, vente.sum(), width=brick_days * 0.85, bottom=achat.sum(), color=ROUGE,
                alpha=0.8)
        tprix.append(mdates.date2num(datetime.fromtimestamp((tsb + brick_ms) / 1000, tz=timezone.utc)))
        close.append(c)

    # --- VP_fullNY : lignes de référence (session entière) sur le panneau principal ---
    xa, xb = mdates.date2num(datetime.fromtimestamp(t0/1000, tz=timezone.utc)), \
        mdates.date2num(datetime.fromtimestamp(t1/1000, tz=timezone.utc))
    ax.axhspan(val_f, vah_f, xmin=0, xmax=1, color="#93c5fd", alpha=0.12, zorder=0)
    ax.hlines([vah_f, val_f], xa, xb, color=POC_FULL, lw=1.0, ls="--", alpha=0.7,
              label=f"VAH/VAL session ({coverage:.0%})")
    ax.hlines(poc_f, xa, xb, color=POC_FULL, lw=1.8, label=f"POC session (VP_fullNY) {poc_f:,.0f}")

    # --- DYNAMIQUE : le POC GLISSANT suit la valeur en formation (près du prix) ---
    xdev = [mdates.date2num(datetime.fromtimestamp((ts + brick_ms) / 1000, tz=timezone.utc))
            for ts, *_ in dev]
    ax.plot(xdev, [p for _, p, _, _ in dev], color=POC_DEV, lw=1.6,
            label=f"POC glissant ({meta['window']})")

    ax.plot(tprix, close, color="#111", lw=1.2, marker=".", ms=3, alpha=0.85,
            label=f"Prix (close {meta['tf']})")
    ax.set_ylabel("Prix ($)")
    ax.set_xlim(xa, xb)
    ax.legend(loc="lower left", fontsize=8.5, framealpha=0.92, ncol=2)
    ax.set_title(f"Sous-VP {meta['tf']} sur prix × temps — couleur = delta/volume "
                 f"(intensité d'agression)", fontsize=12, weight="bold", color=ENCRE)

    # --- panneau marginal : VP_fullNY (volume par niveau, achat/vente) ---
    niv = np.array(sorted(full))
    a_f = np.array([full[l][0] for l in niv])
    v_f = np.array([full[l][1] for l in niv])
    axm.barh(niv, a_f, height=tick, color=VERT, alpha=0.85)
    axm.barh(niv, v_f, left=a_f, height=tick, color=ROUGE, alpha=0.85)
    axm.axhspan(val_f, vah_f, color="#93c5fd", alpha=0.18)
    axm.axhline(poc_f, color=POC_FULL, lw=1.8)
    axm.set_title("VP_fullNY", fontsize=10, weight="bold", color=ENCRE)
    axm.tick_params(axis="y", labelleft=False)
    axm.tick_params(axis="x", labelsize=7)
    axm.set_xlabel("volume (BTC)", fontsize=8)
    axm.grid(alpha=0.15)

    # --- axe temps en heure de New York ---
    def _fmt(x, pos=None):
        dt = mdates.num2date(x, tz=NY)
        return dt.strftime("%H:%M\n%d %b") if (dt.hour == 0 or pos == 0) else dt.strftime("%H:%M")

    for a in (ax, axv):
        a.xaxis.set_major_locator(mdates.HourLocator(tz=NY))
        a.grid(which="major", axis="x", color=ENCRE, alpha=0.18, lw=0.6)
        a.grid(which="major", axis="y", alpha=0.18)
    ax.tick_params(axis="x", labelbottom=False)
    axv.xaxis.set_major_formatter(FuncFormatter(_fmt))
    axv.set_xlabel("heure de New York", fontsize=9, color=ENCRE)
    axv.set_ylabel(f"volume / {meta['tf']}\n(BTC)", fontsize=8.5)
    axv.bar(np.nan, 0, color=VERT, alpha=0.8, label="achat")
    axv.bar(np.nan, 0, color=ROUGE, alpha=0.8, label="vente")
    axv.legend(loc="upper left", fontsize=8, ncol=2)

    sm = ScalarMappable(norm=norm, cmap=CMAP)
    cb = fig.colorbar(sm, ax=(axm,), pad=0.02, fraction=0.5, aspect=40, extend="both")
    cb.set_ticks([-RSAT, 0, RSAT])
    cb.set_label("delta / volume  (vendeurs ← 0 → acheteurs)", fontsize=8)

    fig.suptitle(f"{meta['session'].upper()} {meta['date']} ({meta['plage']}) — "
                 f"sous-VP {meta['tf']} vs VP_fullNY  ·  POC {poc_f:,.0f}  "
                 f"VAH {vah_f:,.0f}  VAL {val_f:,.0f}  ({coverage:.0%})",
                 fontsize=12.5, weight="bold", color=ENCRE)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="VP multi-échelles (analyse order-flow).")
    ap.add_argument("--date", default="2026-03-10", help="jour NY, ex. 2026-03-10")
    ap.add_argument("--session", default="ny", choices=["asia", "london", "ny"])
    ap.add_argument("--tf", default="5m", help="taille des sous-VP : 1m, 5m, 15m, 30m…")
    ap.add_argument("--coverage", type=float, default=0.70, help="value area (VAL/VAH), défaut 0.70")
    ap.add_argument("--window", default="60m", help="fenêtre du POC glissant, ex. 60m")
    ap.add_argument("--tick", type=float, default=25.0, help="taille d'un niveau de prix ($)")
    args = ap.parse_args()

    brick_ms = parse_tf(args.tf)
    window_bricks = max(1, round(parse_tf(args.window) / brick_ms))
    t0, t1, plage = session_bounds(args.date, args.session)
    print(f"[{args.session} {args.date}] {plage}  |  sous-VP {args.tf}, VA {args.coverage:.0%}, "
          f"POC glissant {args.window} ({window_bricks} briques)")
    bricks = stream_bricks(DB, t0, t1, brick_ms, args.tick)
    if not bricks:
        raise SystemExit("aucun tick sur cette fenêtre.")
    full = merge_profiles(bricks)
    dev = rolling_va(bricks, window_bricks, args.coverage)
    poc, vah, val = value_area(full, args.coverage)
    vol = sum(a + v for a, v in full.values())
    print(f"  {len(bricks)} briques · VP_fullNY : POC {poc:,.0f} · VAH {vah:,.0f} · "
          f"VAL {val:,.0f} · volume {vol:,.0f} BTC")

    meta = {"date": args.date, "session": args.session, "tf": args.tf, "plage": plage,
            "window": args.window}
    out = FIG_DIR / f"lecon-09-vp-{args.session}-{args.tf}.png"
    build_figure(bricks, full, dev, t0, t1, brick_ms, args.tick, args.coverage, meta, out)
    print(f"  figure -> {out}")


if __name__ == "__main__":
    main()
