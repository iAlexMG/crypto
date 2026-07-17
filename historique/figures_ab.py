#!/usr/bin/env python3
r"""Figures A/B des pages hist-* — gabarit VERSIONNE, v2 « 6 figures » (stdlib seule).

Deux triplets symetriques par exchange (decision utilisateur 2026-07-13) :

  PLEIN HISTORIQUE (fenetre commune, resolution 1 m) :
    1. <slug>-historique-complet.svg    — 40 jours, les DEUX voies superposees
       (bande high-low + cloture par colonne de pixels) ;
    2. <slug>-correlation-historique.svg — correlation temporelle : r(decalage)
       des rendements 1 m, pic au decalage 0 (max = correlation mesuree) ;
    3. <slug>-ecart-historique.svg      — cloture B − cloture A ($) au fil des
       40 jours, meme resolution que la figure 1.

  JOURNEE TEMOIN (2026-06-05) :
    4. <slug>-journee-temoin.svg        — la journee par les deux voies ;
       exchanges a voie B en TICKS (Binance, Bybit) : panneaux 1 s + bandeau
       zoom de 20 s ou chaque point est un trade ; voie B en 1 m : panneaux 1 m ;
    5. <slug>-correlation-journee.svg   — r(decalage) des rendements sur la
       journee, a la resolution de la figure 4 (1 s ou 1 m) ;
    6. <slug>-ecart-journee.svg         — cloture B − cloture A ($) au fil de la
       journee, meme resolution que la figure 4.

POURQUOI LES RENDEMENTS pour les correlations temporelles : les prix sont
autocorreles (r(cloture, decalage voisin) ≈ 1 partout — aucune information) ;
les rendements donnent le pic net « max = corr » attendu. Le r des clotures
reste affiche en note.

PLAGE COMMUNE (decision utilisateur 2026-07-12) : toutes les figures partagent
la fenetre 2026-06-01 00:00 -> 2026-07-10 24:00 UTC (40 jours) et la journee
temoin 2026-06-05 (jour le plus actif). Garde-fou : REFUS si une base ne couvre
pas la fenetre (--force pour outrepasser).

CACHE : les series extraites (1 m plein historique, 1 s de la journee, ticks du
zoom) sont mises en cache dans data\_figcache-<slug>.json — les scans de 100 M
de lignes ne se paient qu'une fois ; --no-cache pour forcer la relecture.

Palette du pilier : bleu #3987e5 = voie A, aqua #199e70 = voie B.

EXEMPLES :
  python figures_ab.py --venue kucoin           # un exchange, tout par defaut
  python figures_ab.py --tous                   # les 4 exchanges valides
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3

from compare_ab import Voie, day_ms, pearson

BG, GRID, DIAG = "#0d1117", "#21262d", "#383835"
BLEU, AQUA, GRIS, BLANC, SOUS = "#3987e5", "#199e70", "#898781", "#ffffff", "#c3c2b7"
MOIS_FR = {5: "mai", 6: "juin", 7: "juil.", 8: "août", 9: "sept."}

FENETRE_START = "2026-06-01"
FENETRE_END = "2026-07-11"        # exclus -> dernier jour plein : 2026-07-10
JUMEAUX_DAY = "2026-06-05"
ZOOM_START = "16:12:10"           # fenetre tick de 20 s au coeur du decrochage
ZOOM_SECONDES = 20
LAGS = 30                         # r(decalage) calcule de -LAGS a +LAGS

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Registre : la voie A est toujours data\BTCUSDT-<slug>-perp-api.db.
VENUES = {
    "binance": dict(nom="Binance", b="BTCUSDT-binance-perp-qt.db", mult_b=1.0,
                    b_nature="ticks Quantower"),
    "bybit":   dict(nom="Bybit",   b="BTCUSDT-bybit-perp-qt.db",   mult_b=1.0,
                    b_nature="ticks Quantower"),
    "okx":     dict(nom="OKX",     b="BTCUSDT-okx-perp-qt1m.db",   mult_b=0.01,
                    b_nature="chandelles 1 m Quantower"),
    "kucoin":  dict(nom="KuCoin",  b="BTCUSDT-kucoin-perp-qt1m.db", mult_b=0.001,
                    b_nature="chandelles 1 m Quantower"),
    "bitget":  dict(nom="Bitget",  b="BTCUSDT-bitget-perp-rest1m.db", mult_b=1.0,
                    b_nature="chandelles 1 m de l’API de marché"),
}


def fr_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def fr_r(x: float, dec: int) -> str:
    """r a la francaise, decimales groupees par 3 (0,999 999 96)."""
    s = f"{x:.{dec}f}".replace(".", ",")
    ent, _, d = s.partition(",")
    grp = " ".join(d[i:i + 3] for i in range(0, len(d), 3))
    return f"{ent},{grp}"


def fr_money(x: float) -> str:
    """Montant $ court : 0,8 $ / 12 $ / 1 250 $."""
    if abs(x) < 10:
        return f"{x:.1f}".replace(".", ",") + " $"
    return fr_int(round(x)) + " $"


def jour_fr(d: dt.date) -> str:
    return f"{d.day}{'er' if d.day == 1 else ''} {MOIS_FR[d.month]}"


def main() -> int:
    p = argparse.ArgumentParser(description="Figures A/B (6 par page) du gabarit hist-*.")
    p.add_argument("--venue", choices=sorted(VENUES),
                   help="exchange du registre (drapeaux explicites prioritaires)")
    p.add_argument("--tous", action="store_true",
                   help="les 5 exchanges du registre (binance, bybit, okx, kucoin, bitget)")
    p.add_argument("--a", default=None, help="base voie A (defaut : registre)")
    p.add_argument("--b", default=None, help="base voie B (defaut : registre)")
    p.add_argument("--start", default=FENETRE_START)
    p.add_argument("--end", default=FENETRE_END, help="fin fenetre UTC (YYYY-MM-DD, EXCLUS)")
    p.add_argument("--mult-b", type=float, default=None, help="defaut : registre")
    p.add_argument("--venue-nom", default=None, help="nom affiche (defaut : registre)")
    p.add_argument("--b-nature", default=None,
                   help="nature de la voie B pour les legendes (defaut : registre)")
    p.add_argument("--jumeaux-day", default=JUMEAUX_DAY,
                   help="journee temoin (YYYY-MM-DD)")
    p.add_argument("--out", default=os.path.join("site-content", "assets", "figures"))
    p.add_argument("--force", action="store_true",
                   help="generer meme si une base ne couvre pas toute la fenetre")
    p.add_argument("--no-cache", action="store_true",
                   help="ignorer/reconstruire le cache des series")
    args = p.parse_args()

    if not args.tous and not args.venue:
        p.error("--venue <slug> ou --tous requis")
    rc = 0
    slugs = ["binance", "bybit", "okx", "kucoin", "bitget"] if args.tous else [args.venue]
    for slug in slugs:
        cfg = VENUES[slug]
        ns = argparse.Namespace(
            venue=slug,
            venue_nom=args.venue_nom or cfg["nom"],
            a=args.a or os.path.join(DATA, f"BTCUSDT-{slug}-perp-api.db"),
            b=args.b or os.path.join(DATA, cfg["b"]),
            mult_b=args.mult_b if args.mult_b is not None else cfg["mult_b"],
            b_nature=args.b_nature or cfg["b_nature"],
            start=args.start, end=args.end, jumeaux_day=args.jumeaux_day,
            out=args.out, force=args.force, no_cache=args.no_cache)
        print(f"\n== {ns.venue_nom} ==")
        rc = max(rc, generer(ns))
    return rc


# ---------------------------------------------------------------- extraction

def _connect_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _b_kind(path: str) -> str:
    conn = _connect_ro(path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    return "trades" if "trades" in names else "bars"


def _scan_day_seconds(path: str, t0: int, t1: int) -> list:
    """Serie 1 s d'une journee de trades : [sec, close, high, low] tries."""
    conn = _connect_ro(path)
    cur = conn.execute(
        "SELECT ts, price FROM trades WHERE ts>=? AND ts<? ORDER BY trade_id",
        (t0, t1))
    sec: dict[int, list] = {}
    while True:
        chunk = cur.fetchmany(500_000)
        if not chunk:
            break
        for ts, price in chunk:
            s = ts // 1000
            c = sec.get(s)
            if c is None:
                sec[s] = [price, price, price]      # close, high, low
            else:
                c[0] = price
                if price > c[1]:
                    c[1] = price
                if price < c[2]:
                    c[2] = price
    conn.close()
    return [[s, *sec[s]] for s in sorted(sec)]


def _scan_zoom(path: str, t0: int, t1: int) -> list:
    conn = _connect_ro(path)
    rows = conn.execute(
        "SELECT ts, price FROM trades WHERE ts>=? AND ts<? ORDER BY trade_id",
        (t0, t1)).fetchall()
    conn.close()
    return [list(r) for r in rows]


def extraire(args) -> dict | None:
    """Toutes les series necessaires aux 6 figures, avec cache json."""
    cache = os.path.join(DATA, f"_figcache-{args.venue}.json")
    if not args.no_cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("start") == args.start and d.get("end") == args.end \
                and d.get("jour") == args.jumeaux_day:
            print(f"cache : {cache}")
            return d

    t0, t1 = day_ms(args.start), day_ms(args.end)
    A = Voie("A", args.a, 1.0)
    B = Voie("B", args.b, args.mult_b)
    for v in (A, B):
        cov = v.coverage()
        manque = (cov is None or cov[0] > t0 + 60_000 or cov[1] < t1 - 60_000)
        if manque:
            def iso(ms): return dt.datetime.fromtimestamp(
                ms / 1000, dt.timezone.utc).isoformat()
            etendue = "base vide" if cov is None else f"{iso(cov[0])} -> {iso(cov[1])}"
            msg = (f"couverture voie {v.name} incomplete ({etendue}) pour la fenetre "
                   f"{args.start} -> {args.end} (exclus)")
            if not args.force:
                print(f"REFUS : {msg}. Completer la base ou --force.")
                return None
            print(f"ATTENTION (--force) : {msg}.")
    for v in (A, B):
        v.scan(t0, t1)
        print(f"Voie {v.name} ({v.kind}) : {fr_int(v.rows)} lignes, {len(v.minutes)} minutes")

    d = {"start": args.start, "end": args.end, "jour": args.jumeaux_day,
         "b_kind": B.kind,
         "minutes_a": [[m, *A.minutes[m]] for m in sorted(A.minutes)],
         "minutes_b": [[m, *B.minutes[m]] for m in sorted(B.minutes)]}

    if B.kind == "trades":                          # journee en 1 s + zoom tick
        j0 = day_ms(args.jumeaux_day)
        j1 = j0 + 86_400_000
        hh, mm, ss = (int(x) for x in ZOOM_START.split(":"))
        z0 = j0 + (hh * 3600 + mm * 60 + ss) * 1000
        z1 = z0 + ZOOM_SECONDES * 1000
        print("journee temoin : agregation 1 s + ticks du zoom…")
        d["day1s_a"] = _scan_day_seconds(args.a, j0, j1)
        d["day1s_b"] = _scan_day_seconds(args.b, j0, j1)
        d["zoom_a"] = _scan_zoom(args.a, z0, z1)
        d["zoom_b"] = _scan_zoom(args.b, z0, z1)

    with open(cache, "w", encoding="utf-8") as f:
        json.dump(d, f)
    print(f"cache écrit : {cache}")
    return d


# ---------------------------------------------------------------- calculs

def series_communes(rows_a: list, rows_b: list):
    """Aligne deux series [cle, ...] -> (cles, closes_a, closes_b, ohlc?)."""
    da = {r[0]: r[1:] for r in rows_a}
    db = {r[0]: r[1:] for r in rows_b}
    keys = sorted(set(da) & set(db))
    return keys, da, db


def r_par_decalage(keys: list, da: dict, db: dict, idx_close: int, pas: int):
    """r(decalage) des rendements ; series alignees sur cles CONTIGUES (pas)."""
    # rendements sur paires contigues seulement (trous exclus)
    ra, rb, ks = [], [], []
    prev = None
    for k in keys:
        if prev is not None and k - prev == pas:
            pa, pb = da[prev][idx_close], db[prev][idx_close]
            if pa and pb:
                ra.append(da[k][idx_close] / pa - 1)
                rb.append(db[k][idx_close] / pb - 1)
                ks.append(k)
        prev = k
    out = []
    n = len(ra)
    for lag in range(-LAGS, LAGS + 1):
        if lag >= 0:
            xa, xb = ra[:n - lag] if lag else ra, rb[lag:]
        else:
            xa, xb = ra[-lag:], rb[:n + lag]
        out.append((lag, pearson(xa, xb) if len(xa) > 2 else 0.0))
    return out, pearson(ra, rb)


# ---------------------------------------------------------------- figures

def _entete(w, h, titre, sous_titre, aria):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'font-family="system-ui,-apple-system,\'Segoe UI\',sans-serif" role="img" '
            f'aria-label="{aria}">',
            f'<rect width="{w}" height="{h}" fill="{BG}"/>',
            f'<text x="76" y="34" font-size="19" fill="{BLANC}" text-anchor="start" '
            f'font-weight="600">{titre}</text>',
            f'<text x="76" y="56" font-size="12.5" fill="{SOUS}" text-anchor="start">'
            f'{sous_titre}</text>']


def _legende(s, x, couleur, texte):
    s += [f'<rect x="{x}" y="70" width="10" height="10" rx="2" fill="{couleur}"/>',
          f'<text x="{x + 16}" y="79" font-size="12" fill="{BLANC}" text-anchor="start">'
          f'{texte}</text>']


def _grad_prix(lo: float, hi: float, n: int = 3) -> list[float]:
    brut = (hi - lo) / n
    base = 10 ** (len(str(int(brut))) - 1)
    pas = -(-int(brut) // base) * base
    v = -(-int(lo) // pas) * pas
    out = []
    while v < hi:
        out.append(float(v))
        v += pas
    return out


def _colonnes(keys, serie, idx_hi, idx_lo, idx_close, x0, x1, k0, k1):
    """Enveloppe par colonne de pixels : [(x, hi, lo, close_dernier)]."""
    ncol = int(x1 - x0)
    cols: dict[int, list] = {}
    for k in keys:
        q = serie[k]
        c = int((k - k0) / (k1 - k0) * (ncol - 1))
        e = cols.get(c)
        hi = q[idx_hi] if idx_hi is not None else q[idx_close]
        lo = q[idx_lo] if idx_lo is not None else q[idx_close]
        if e is None:
            cols[c] = [hi, lo, q[idx_close]]
        else:
            if hi > e[0]:
                e[0] = hi
            if lo < e[1]:
                e[1] = lo
            e[2] = q[idx_close]
    return [(x0 + c, *cols[c]) for c in sorted(cols)]


def _trace_enveloppe(s, cols, sy, couleur, op_bande, ep_ligne):
    band_up = " ".join(f"{x:.1f},{sy(hi):.1f}" for x, hi, lo, cl in cols)
    band_dn = " ".join(f"{x:.1f},{sy(lo):.1f}" for x, hi, lo, cl in reversed(cols))
    line = " ".join(f"{x:.1f},{sy(cl):.1f}" for x, hi, lo, cl in cols)
    s += [f'<polygon points="{band_up} {band_dn}" fill="{couleur}" fill-opacity="{op_bande}"/>',
          f'<polyline points="{line}" fill="none" stroke="{couleur}" stroke-width="{ep_ligne}"/>']


def fig_historique(args, keys, da, db) -> None:
    """Fig 1 : 40 jours, les deux voies superposees (B par-dessus A)."""
    x0, x1, y0, y1 = 76.0, 896.0, 440.0, 96.0
    k0, k1 = keys[0], keys[-1] + 1
    ca = _colonnes(keys, da, 1, 2, 3, x0, x1, k0, k1)
    cb = _colonnes(keys, db, 1, 2, 3, x0, x1, k0, k1)
    lo = min(min(c[2] for c in ca), min(c[2] for c in cb))
    hi = max(max(c[1] for c in ca), max(c[1] for c in cb))
    marge = (hi - lo) * 0.05
    lo, hi = lo - marge, hi + marge

    def sy(p):
        return y0 + (p - lo) / (hi - lo) * (y1 - y0)

    d0 = dt.date.fromisoformat(args.start)
    d1 = dt.date.fromisoformat(args.end) - dt.timedelta(days=1)
    s = _entete(920, 520,
                "Quarante jours, deux historiques superposés",
                f"BTCUSDT perpétuel {args.venue_nom}, {jour_fr(d0)} → {jour_fr(d1)} 2026 (UTC) — "
                f"bande high-low et clôture de chaque minute, voie B tracée par-dessus la voie A",
                f"L’historique complet {args.venue_nom} par les deux voies, superposées")
    _legende(s, 76, BLEU, "voie A — archives officielles")
    _legende(s, 360, AQUA, f"voie B — {args.b_nature}")
    for gv in _grad_prix(lo, hi, 4):
        gy = sy(gv)
        s += [f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}" stroke="{GRID}"/>',
              f'<text x="{x0 - 8}" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" '
              f'text-anchor="end">{fr_int(round(gv))}</text>']
    _trace_enveloppe(s, ca, sy, BLEU, 0.30, 1.6)
    _trace_enveloppe(s, cb, sy, AQUA, 0.30, 0.9)
    d = d0
    while d <= d1:
        if (d - d0).days % 7 == 0 and (d1 - d).days >= 3 or d == d1:
            gx = x0 + ((day_ms(d.isoformat()) // 60000) - k0) / (k1 - k0) * (x1 - x0)
            s.append(f'<text x="{gx:.1f}" y="462" font-size="11" fill="{GRIS}" '
                     f'text-anchor="middle">{jour_fr(d)}</text>')
        d += dt.timedelta(days=1)
    s.append(f'<text x="{x0}" y="496" font-size="12" fill="{SOUS}" text-anchor="start">'
             f'Une seule courbe visible : à cette échelle, les deux voies sont indiscernables '
             f'— l’aqua recouvre le bleu point pour point.</text>')
    s.append('</svg>')
    _write(args, f"{args.venue}-historique-complet.svg", "\n".join(s))


def fig_correlation(args, lags, r_pic, r_clot, nom_fig, portee, unite) -> None:
    """Fig 2/5 : correlation temporelle — pic de r au decalage 0."""
    x0, x1, y0, y1 = 76.0, 896.0, 396.0, 96.0
    rmin = min(r for _, r in lags)
    y_lo = min(-0.05, rmin - 0.05)
    y_hi = 1.05

    def sx(lag):
        return x0 + (lag + LAGS) / (2 * LAGS) * (x1 - x0)

    def sy(r):
        return y0 + (r - y_lo) / (y_hi - y_lo) * (y1 - y0)

    s = _entete(920, 470,
                "La corrélation est au rendez-vous — et seulement au décalage zéro",
                f"Corrélation croisée des rendements {unite} entre voie A et voie B décalée — {portee}",
                f"Corrélation temporelle {args.venue_nom} : pic de corrélation au décalage 0")
    for gv in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = sy(gv)
        lab = fr_r(gv, 2) if gv not in (0.0, 1.0) else ("0" if gv == 0 else "1")
        s += [f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}" stroke="{GRID}"/>',
              f'<text x="{x0 - 8}" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" '
              f'text-anchor="end">{lab}</text>']
    for lag in range(-LAGS, LAGS + 1, 10):
        gx = sx(lag)
        s.append(f'<text x="{gx:.1f}" y="418" font-size="11" fill="{GRIS}" '
                 f'text-anchor="middle">{"+" if lag > 0 else ""}{lag}</text>')
    # tiges + points, pic en aqua
    for lag, r in lags:
        gx, gy = sx(lag), sy(r)
        if lag == 0:
            s += [f'<line x1="{gx:.1f}" y1="{sy(0):.1f}" x2="{gx:.1f}" y2="{gy:.1f}" '
                  f'stroke="{AQUA}" stroke-width="2.2"/>',
                  f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="4.5" fill="{AQUA}"/>']
        else:
            s += [f'<line x1="{gx:.1f}" y1="{sy(0):.1f}" x2="{gx:.1f}" y2="{gy:.1f}" '
                  f'stroke="{BLEU}" stroke-width="1.4" stroke-opacity="0.75"/>',
                  f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="2.4" fill="{BLEU}"/>']
    px, py = sx(0), sy(lags[LAGS][1])
    s += [f'<text x="{px + 14:.1f}" y="{py - 16:.1f}" font-size="22" fill="{BLANC}" '
          f'text-anchor="start" font-weight="600">r = {fr_r(r_pic, 5)}</text>',
          f'<text x="{px + 14:.1f}" y="{py + 4:.1f}" font-size="12" fill="{SOUS}" '
          f'text-anchor="start">au décalage 0 — partout ailleurs, plus rien</text>',
          f'<text x="{x0}" y="452" font-size="12" fill="{SOUS}" text-anchor="start">'
          f'Clôtures brutes : r = {fr_r(r_clot, 8)}. Les rendements, eux, ne se ressemblent '
          f'que si chaque {unite.replace("1 ", "")} est la même des deux côtés — le pic unique le prouve.</text>',
          f'<text x="486" y="436" font-size="11" fill="{GRIS}" text-anchor="middle">'
          f'décalage de la voie B ({unite.replace("1 ", "")}s)</text>',
          '</svg>']
    _write(args, nom_fig, "\n".join(s))


def fig_ecart(args, keys, da, db, idx_close, nom_fig, titre, sous_titre,
              axe_temps, k0, k1) -> None:
    """Fig 3/6 : cloture B − cloture A ($) au fil du temps."""
    x0, x1, y0, y1 = 76.0, 896.0, 396.0, 96.0
    diffs = {k: db[k][idx_close] - da[k][idx_close] for k in keys}
    dmax = max(abs(v) for v in diffs.values())
    zero = dmax == 0
    lim = max(dmax * 1.15, 0.5)

    def sy(v):
        return (y0 + y1) / 2 - v / lim * (y0 - y1) / 2

    s = _entete(920, 470, titre, sous_titre,
                f"L’écart de clôture entre les deux voies {args.venue_nom}")
    # pas « propre » (gere lim < 1 — cas ecart nul, ou _grad_prix boucle)
    pas = next(c for c in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)
               if c >= lim / 2.5)
    grads, v = [], pas
    while v <= lim:
        grads.append(v)
        v += pas
    for gv in grads:
        for sgn in (1, -1):
            gy = sy(gv * sgn)
            s += [f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}" stroke="{GRID}"/>',
                  f'<text x="{x0 - 8}" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" '
                  f'text-anchor="end">{"+" if sgn > 0 else "−"}{fr_money(gv)}</text>']
    s += [f'<line x1="{x0}" y1="{sy(0):.1f}" x2="{x1}" y2="{sy(0):.1f}" '
          f'stroke="{DIAG}" stroke-width="1.2"/>',
          f'<text x="{x0 - 8}" y="{sy(0) + 4:.1f}" font-size="11" fill="{GRIS}" '
          f'text-anchor="end">0</text>']
    cols = _colonnes(keys, {k: [diffs[k]] for k in keys}, None, None, 0, x0, x1, k0, k1)
    if zero:
        s += [f'<polyline points="{x0},{sy(0):.1f} {x1},{sy(0):.1f}" fill="none" '
              f'stroke="{AQUA}" stroke-width="1.6"/>',
              f'<text x="486" y="200" font-size="26" fill="{BLANC}" text-anchor="middle" '
              f'font-weight="600">0,00 $</text>',
              f'<text x="486" y="228" font-size="14" fill="{SOUS}" text-anchor="middle">'
              f'l’écart est nul du premier au dernier point — les deux voies coïncident '
              f'exactement</text>']
    else:
        _trace_enveloppe(s, cols, sy, AQUA, 0.35, 1.0)
        s.append(f'<text x="{x0}" y="452" font-size="12" fill="{SOUS}" text-anchor="start">'
                 f'Écart maximal : {fr_money(dmax)} — moyenne des écarts absolus : '
                 f'{fr_money(sum(abs(v) for v in diffs.values()) / len(diffs))}.</text>')
    s += axe_temps
    s.append('</svg>')
    _write(args, nom_fig, "\n".join(s))


def fig_journee_1m(args, keys, da, db) -> None:
    """Fig 4 (voie B en 1 m) : deux panneaux, bande high-low + cloture."""
    d = dt.date.fromisoformat(args.jumeaux_day)
    m0 = day_ms(args.jumeaux_day) // 60000
    m1 = m0 + 1440
    ka = [k for k in keys if m0 <= k < m1]
    lo = min(min(da[k][2] for k in ka), min(db[k][2] for k in ka))
    hi = max(max(da[k][1] for k in ka), max(db[k][1] for k in ka))
    marge = (hi - lo) * 0.06
    lo, hi = lo - marge, hi + marge
    x0, x1 = 76.0, 896.0

    def sx(m):
        return x0 + (m - m0) / 1440 * (x1 - x0)

    def panel(serie, y_top, y_bot, couleur, nom):
        def sy(p):
            return y_bot + (p - lo) / (hi - lo) * (y_top - y_bot)
        band_up = " ".join(f"{sx(k):.1f},{sy(serie[k][1]):.1f}" for k in ka)
        band_dn = " ".join(f"{sx(k):.1f},{sy(serie[k][2]):.1f}" for k in reversed(ka))
        line = " ".join(f"{sx(k):.1f},{sy(serie[k][3]):.1f}" for k in ka)
        out = [f'<polygon points="{band_up} {band_dn}" fill="{couleur}" fill-opacity="0.22"/>',
               f'<polyline points="{line}" fill="none" stroke="{couleur}" stroke-width="1.1"/>',
               f'<text x="{x0}" y="{y_top - 8:.1f}" font-size="12" fill="{couleur}" '
               f'text-anchor="start" font-weight="600">{nom}</text>']
        for gv in _grad_prix(lo, hi):
            gy = sy(gv)
            out += [f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}" stroke="{GRID}"/>',
                    f'<text x="{x0 - 8}" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" '
                    f'text-anchor="end">{fr_int(round(gv))}</text>']
        return out

    s = _entete(920, 580,
                "La journée témoin, reconstruite par les deux voies",
                f"BTCUSDT perpétuel {args.venue_nom}, {jour_fr(d)} 2026 (UTC) — bande high-low "
                f"et clôture de chaque minute",
                f"La journée témoin {args.venue_nom} par les deux voies, A en haut, B en bas")
    _legende(s, 76, BLEU, f"voie A — {fr_int(len(ka))} chandelles agrégées des trades d’archives")
    _legende(s, 440, AQUA, f"voie B — {fr_int(len(ka))} chandelles servies ({args.b_nature})")
    s += panel(da, 106.0, 306.0, BLEU, "voie A")
    s += panel(db, 336.0, 536.0, AQUA, "voie B")
    for hh in (0, 4, 8, 12, 16, 20, 24):
        gx = sx(m0 + hh * 60)
        s.append(f'<text x="{gx:.1f}" y="556" font-size="11" fill="{GRIS}" '
                 f'text-anchor="middle">{hh:02d}:00</text>')
    s.append('</svg>')
    _write(args, f"{args.venue}-journee-temoin.svg", "\n".join(s))


def fig_journee_ticks(args, day_a, day_b, zoom_a, zoom_b) -> None:
    """Fig 4 (voie B en ticks) : deux panneaux 1 s + bandeau zoom de 20 s en ticks."""
    d = dt.date.fromisoformat(args.jumeaux_day)
    s0 = day_ms(args.jumeaux_day) // 1000
    s1 = s0 + 86_400
    da = {r[0]: r[1:] for r in day_a}      # sec -> [close, high, low]
    db = {r[0]: r[1:] for r in day_b}
    lo = min(min(q[2] for q in da.values()), min(q[2] for q in db.values()))
    hi = max(max(q[1] for q in da.values()), max(q[1] for q in db.values()))
    marge = (hi - lo) * 0.06
    lo, hi = lo - marge, hi + marge
    x0, x1 = 76.0, 896.0

    def sx(sec):
        return x0 + (sec - s0) / 86_400 * (x1 - x0)

    def panel(serie, y_top, y_bot, couleur, nom):
        def sy(p):
            return y_bot + (p - lo) / (hi - lo) * (y_top - y_bot)
        cols = _colonnes(sorted(serie), serie, 1, 2, 0, x0, x1, s0, s1)
        out = []
        band_up = " ".join(f"{x:.1f},{sy(h):.1f}" for x, h, l, c in cols)
        band_dn = " ".join(f"{x:.1f},{sy(l):.1f}" for x, h, l, c in reversed(cols))
        line = " ".join(f"{x:.1f},{sy(c):.1f}" for x, h, l, c in cols)
        out += [f'<polygon points="{band_up} {band_dn}" fill="{couleur}" fill-opacity="0.22"/>',
                f'<polyline points="{line}" fill="none" stroke="{couleur}" stroke-width="1.0"/>',
                f'<text x="{x0}" y="{y_top - 8:.1f}" font-size="12" fill="{couleur}" '
                f'text-anchor="start" font-weight="600">{nom}</text>']
        for gv in _grad_prix(lo, hi):
            gy = sy(gv)
            out += [f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}" stroke="{GRID}"/>',
                    f'<text x="{x0 - 8}" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" '
                    f'text-anchor="end">{fr_int(round(gv))}</text>']
        return out

    s = _entete(920, 760,
                "La journée témoin, seconde par seconde — et le zoom au trade près",
                f"BTCUSDT perpétuel {args.venue_nom}, {jour_fr(d)} 2026 (UTC) — enveloppe et "
                f"dernier prix de chaque seconde ; en bas, vingt secondes où chaque point est un trade",
                f"La journée témoin {args.venue_nom} par les deux voies, avec zoom tick par tick")
    _legende(s, 76, BLEU, "voie A — trades des archives officielles")
    _legende(s, 440, AQUA, f"voie B — {args.b_nature}")
    s += panel(da, 106.0, 266.0, BLEU, "voie A")
    s += panel(db, 296.0, 456.0, AQUA, "voie B")
    for hh in (0, 4, 8, 12, 16, 20, 24):
        gx = sx(s0 + hh * 3600)
        s.append(f'<text x="{gx:.1f}" y="478" font-size="11" fill="{GRIS}" '
                 f'text-anchor="middle">{hh:02d}:00</text>')

    # ---- bandeau zoom : chaque point est un trade, les deux voies superposees
    hh, mm, ss = (int(x) for x in ZOOM_START.split(":"))
    z0 = (day_ms(args.jumeaux_day) + (hh * 3600 + mm * 60 + ss) * 1000)
    z1 = z0 + ZOOM_SECONDES * 1000
    zy0, zy1 = 720.0, 530.0
    zlo = min(min(p for _, p in zoom_a), min(p for _, p in zoom_b))
    zhi = max(max(p for _, p in zoom_a), max(p for _, p in zoom_b))
    zmarge = (zhi - zlo) * 0.08
    zlo, zhi = zlo - zmarge, zhi + zmarge

    def zx(ts):
        return x0 + (ts - z0) / (z1 - z0) * (x1 - x0)

    def zy(p):
        return zy0 + (p - zlo) / (zhi - zlo) * (zy1 - zy0)

    # repere : ou est le zoom dans la journee (trait sous le panneau B)
    gx = sx(z0 // 1000)
    s += [f'<line x1="{gx:.1f}" y1="456" x2="{gx:.1f}" y2="466" stroke="{BLANC}" stroke-width="2"/>',
          f'<line x1="{gx:.1f}" y1="466" x2="76" y2="512" stroke="{DIAG}" stroke-width="1"/>',
          f'<line x1="{gx:.1f}" y1="466" x2="896" y2="512" stroke="{DIAG}" stroke-width="1"/>',
          f'<rect x="{x0 - 6}" y="{zy1 - 18}" width="{x1 - x0 + 12}" height="{zy0 - zy1 + 42}" '
          f'fill="none" stroke="{GRID}" rx="6"/>',
          f'<text x="{x0}" y="{zy1 - 26:.1f}" font-size="12.5" fill="{BLANC}" '
          f'text-anchor="start" font-weight="600">Zoom : {ZOOM_SECONDES} secondes au cœur du '
          f'décrochage ({ZOOM_START} → :{(ss + ZOOM_SECONDES) % 60:02d} UTC) — '
          f'{fr_int(len(zoom_a))} trades voie A, {fr_int(len(zoom_b))} voie B</text>']
    for gv in _grad_prix(zlo, zhi, 2):
        gy = zy(gv)
        s += [f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}" stroke="{GRID}"/>',
              f'<text x="{x0 - 8}" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" '
              f'text-anchor="end">{fr_int(round(gv))}</text>']
    pts_a = "".join(f'<circle cx="{zx(t):.1f}" cy="{zy(p):.1f}" r="2.6"/>' for t, p in zoom_a)
    pts_b = "".join(f'<circle cx="{zx(t):.1f}" cy="{zy(p):.1f}" r="1.2"/>' for t, p in zoom_b)
    s += [f'<g fill="{BLEU}" fill-opacity="0.5">{pts_a}</g>',
          f'<g fill="{AQUA}" fill-opacity="0.85">{pts_b}</g>']
    for k in range(0, ZOOM_SECONDES + 1, 5):
        gx = zx(z0 + k * 1000)
        s.append(f'<text x="{gx:.1f}" y="{zy0 + 20:.1f}" font-size="11" fill="{GRIS}" '
                 f'text-anchor="middle">:{(ss + k) % 60:02d}</text>')
    s.append('</svg>')
    _write(args, f"{args.venue}-journee-temoin.svg", "\n".join(s))


# ---------------------------------------------------------------- pilotage

def axe_jours(args, keys, k0, k1, y=418):
    d0 = dt.date.fromisoformat(args.start)
    d1 = dt.date.fromisoformat(args.end) - dt.timedelta(days=1)
    out = []
    d = d0
    while d <= d1:
        if (d - d0).days % 7 == 0 and (d1 - d).days >= 3 or d == d1:
            gx = 76 + ((day_ms(d.isoformat()) // 60000) - k0) / (k1 - k0) * 820
            out.append(f'<text x="{gx:.1f}" y="{y}" font-size="11" fill="{GRIS}" '
                       f'text-anchor="middle">{jour_fr(d)}</text>')
        d += dt.timedelta(days=1)
    return out


def axe_heures(k0, k1, unite_s, y=418):
    out = []
    for hh in (0, 4, 8, 12, 16, 20, 24):
        gx = 76 + (hh * 3600 / unite_s) / (k1 - k0) * 820
        out.append(f'<text x="{gx:.1f}" y="{y}" font-size="11" fill="{GRIS}" '
                   f'text-anchor="middle">{hh:02d}:00</text>')
    return out


def generer(args) -> int:
    d = extraire(args)
    if d is None:
        return 1

    # ---- triplet plein historique (1 m)
    keys, da, db = series_communes(d["minutes_a"], d["minutes_b"])
    r_clot = pearson([da[k][3] for k in keys], [db[k][3] for k in keys])
    ident = sum(1 for k in keys if da[k] == db[k])
    lags, r_rend = r_par_decalage(keys, da, db, 3, 1)
    print(f"fenetre : {fr_int(len(keys))} minutes communes | r clotures {r_clot:.10f} | "
          f"identiques {fr_int(ident)} | r rendements {r_rend:.6f}")

    os.makedirs(args.out, exist_ok=True)
    k0, k1 = keys[0], keys[-1] + 1
    fig_historique(args, keys, da, db)
    d0 = dt.date.fromisoformat(args.start)
    d1 = dt.date.fromisoformat(args.end) - dt.timedelta(days=1)
    fig_correlation(args, lags, r_rend, r_clot,
                    f"{args.venue}-correlation-historique.svg",
                    f"fenêtre entière, {jour_fr(d0)} → {jour_fr(d1)} 2026", "1 minute")
    fig_ecart(args, keys, da, db, 3, f"{args.venue}-ecart-historique.svg",
              "L’écart entre les deux historiques, minute par minute",
              f"Clôture voie B − clôture voie A ($), chaque minute des quarante jours — "
              f"même résolution que la vue d’ensemble",
              axe_jours(args, keys, k0, k1), k0, k1)

    # ---- triplet journee temoin
    jd = dt.date.fromisoformat(args.jumeaux_day)
    if d["b_kind"] == "trades":
        fig_journee_ticks(args, d["day1s_a"], d["day1s_b"], d["zoom_a"], d["zoom_b"])
        jkeys, jda, jdb = series_communes(d["day1s_a"], d["day1s_b"])
        jlags, jr = r_par_decalage(jkeys, jda, jdb, 0, 1)
        jr_clot = pearson([jda[k][0] for k in jkeys], [jdb[k][0] for k in jkeys])
        unite, unite_s = "1 seconde", 1
        js0 = day_ms(args.jumeaux_day) // 1000
        jk0, jk1 = js0, js0 + 86_400
    else:
        m0 = day_ms(args.jumeaux_day) // 60000
        jkeys = [k for k in keys if m0 <= k < m0 + 1440]
        jda, jdb = da, db
        fig_journee_1m(args, jkeys, jda, jdb)
        jlags, jr = r_par_decalage(jkeys, jda, jdb, 3, 1)
        jr_clot = pearson([jda[k][3] for k in jkeys], [jdb[k][3] for k in jkeys])
        unite, unite_s = "1 minute", 60
        jk0, jk1 = m0, m0 + 1440
    print(f"journee : {fr_int(len(jkeys))} points communs ({unite}) | "
          f"r clotures {jr_clot:.10f} | r rendements {jr:.6f}")
    fig_correlation(args, jlags, jr, jr_clot,
                    f"{args.venue}-correlation-journee.svg",
                    f"journée témoin du {jour_fr(jd)} 2026, résolution {unite}", unite)
    idx = 0 if d["b_kind"] == "trades" else 3
    fig_ecart(args, jkeys, jda, jdb, idx, f"{args.venue}-ecart-journee.svg",
              "L’écart sur la journée témoin, au fil des heures",
              f"Clôture voie B − clôture voie A ($), {jour_fr(jd)} 2026 — "
              f"même résolution que la journée ({unite})",
              axe_heures(jk0, jk1, unite_s), jk0, jk1)
    return 0


def _write(args, nom: str, contenu: str) -> None:
    chemin = os.path.join(args.out, nom)
    with open(chemin, "w", encoding="utf-8", newline="\n") as f:
        f.write(contenu + "\n")
    print(f"écrit : {chemin} ({os.path.getsize(chemin) // 1024} Ko)")


if __name__ == "__main__":
    raise SystemExit(main())
