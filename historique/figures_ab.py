#!/usr/bin/env python3
r"""Figures A/B des pages hist-* — gabarit VERSIONNE (stdlib seule).

Genere les trois figures du gabarit hist-bybit pour chaque venue (voie B en
ticks — Binance/Bybit — ou en chandelles 1 m — OKX/KuCoin/Bitget) :

  1. historiques-jumeaux-<venue>.svg  — une journee temoin, voie A (trades agreges
     en 1 m) en haut, voie B en bas : bande high-low + cloture ;
  2. correlation-1m-<venue>.svg       — nuage clotures A (x) contre B (y), 1 point
     sur 12, r=1.9, opacite 0.42 (memes conventions que la figure Bybit) ;
  3. ecart-residuel-<venue>.svg       — par jour, minutes dont la chandelle differe.

PLAGE COMMUNE (decision utilisateur 2026-07-12) : pour que le visiteur compare
les venues entre elles, TOUTES les figures partagent la meme fenetre —
2026-06-01 00:00 -> 2026-07-10 24:00 UTC (40 jours) — et la meme journee temoin,
le 2026-06-05 (jour le plus actif de la fenetre : ~64 k$ -> ~59 k$, volumes ~2x
la moyenne partout). Ces valeurs sont les DEFAUTS ; un garde-fou refuse de
generer si une base ne couvre pas toute la fenetre (--force pour outrepasser).
Meme instrument (BTCUSDT perp) + meme fenetre + memes regles d'arrondi des axes
=> echelles identiques d'une venue a l'autre.

Le registre VENUES porte la config par venue (bases, multiplier de la voie B,
libelles) : `--venue kucoin` suffit, `--tous` regenere les 5 venues d'un coup.
Les drapeaux explicites (--a/--b/--mult-b/...) restent prioritaires.

Reutilise compare_ab.Voie (detection auto trades/bars, --mult-b, lecture seule).
Les chiffres affiches (r, chandelles identiques, rendements) sont RECALCULES ici —
memes definitions que compare_ab.py, verifier la concordance avec sa sortie.

Pourquoi versionne : les figures Bybit avaient ete faites par scripts ephemeres
(seuls les SVG restent) — meme lecon que compare_ab.py, le gabarit merite d'etre
conserve. Palette du pilier : bleu #3987e5 = voie A, aqua #199e70 = voie B.

EXEMPLES :
  python figures_ab.py --venue kucoin           # une venue, tout par defaut
  python figures_ab.py --tous                   # les 5 venues, fenetre commune
  python figures_ab.py --venue bitget --out C:\tmp\figs   # sortie ailleurs
"""
from __future__ import annotations

import argparse
import datetime as dt
import os

from compare_ab import Voie, day_ms, pearson

BG, GRID, DIAG = "#0d1117", "#21262d", "#383835"
BLEU, AQUA, GRIS, BLANC, SOUS = "#3987e5", "#199e70", "#898781", "#ffffff", "#c3c2b7"
MOIS_FR = {6: "juin", 7: "juil.", 8: "août", 9: "sept.", 5: "mai"}

# Fenetre commune a toutes les venues (voir docstring) — --start/--end EXCLUS.
FENETRE_START = "2026-06-01"
FENETRE_END = "2026-07-11"        # exclus -> dernier jour plein : 2026-07-10
JUMEAUX_DAY = "2026-06-05"

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Registre des venues : la voie A est toujours data\BTCUSDT-<slug>-perp-api.db.
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
    """r a la francaise, avec espaces de groupement dans les decimales (0,999 999 96)."""
    s = f"{x:.{dec}f}".replace(".", ",")
    ent, _, d = s.partition(",")
    grp = " ".join(d[i:i + 3] for i in range(0, len(d), 3))
    return f"{ent},{grp}"


def jour_fr(d: dt.date) -> str:
    return f"{d.day}{'er' if d.day == 1 else ''} {MOIS_FR[d.month]}"


def main() -> int:
    p = argparse.ArgumentParser(description="Figures A/B du gabarit hist-*.")
    p.add_argument("--venue", choices=sorted(VENUES),
                   help="venue du registre (config auto ; drapeaux explicites prioritaires)")
    p.add_argument("--tous", action="store_true", help="toutes les venues du registre")
    p.add_argument("--a", default=None, help="base voie A (defaut : registre)")
    p.add_argument("--b", default=None, help="base voie B (defaut : registre)")
    p.add_argument("--start", default=FENETRE_START)
    p.add_argument("--end", default=FENETRE_END, help="fin fenetre UTC (YYYY-MM-DD, EXCLUS)")
    p.add_argument("--mult-b", type=float, default=None, help="defaut : registre")
    p.add_argument("--venue-nom", default=None, help="nom affiche (defaut : registre)")
    p.add_argument("--b-nature", default=None,
                   help="nature de la voie B pour les legendes (defaut : registre)")
    p.add_argument("--jumeaux-day", default=JUMEAUX_DAY,
                   help="journee temoin de la figure 1 (YYYY-MM-DD)")
    p.add_argument("--out", default=os.path.join("site-content", "assets", "figures"))
    p.add_argument("--force", action="store_true",
                   help="generer meme si une base ne couvre pas toute la fenetre")
    args = p.parse_args()

    if not args.tous and not args.venue:
        p.error("--venue <slug> ou --tous requis")
    rc = 0
    for slug in (sorted(VENUES) if args.tous else [args.venue]):
        cfg = VENUES[slug]
        ns = argparse.Namespace(
            venue=slug,
            venue_nom=args.venue_nom or cfg["nom"],
            a=args.a or os.path.join(DATA, f"BTCUSDT-{slug}-perp-api.db"),
            b=args.b or os.path.join(DATA, cfg["b"]),
            mult_b=args.mult_b if args.mult_b is not None else cfg["mult_b"],
            b_nature=args.b_nature or cfg["b_nature"],
            start=args.start, end=args.end, jumeaux_day=args.jumeaux_day,
            out=args.out, force=args.force)
        print(f"\n== {ns.venue_nom} ==")
        rc = max(rc, generer(ns))
    return rc


def generer(args: argparse.Namespace) -> int:
    t0, t1 = day_ms(args.start), day_ms(args.end)
    A = Voie("A", args.a, 1.0)
    B = Voie("B", args.b, args.mult_b)

    # Garde-fou plage commune : chaque voie doit couvrir TOUTE la fenetre, sinon
    # les figures de cette venue ne seraient pas comparables aux autres.
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
                return 1
            print(f"ATTENTION (--force) : {msg}.")

    for v in (A, B):
        v.scan(t0, t1)
        print(f"Voie {v.name} ({v.kind}) : {fr_int(v.rows)} lignes, {len(v.minutes)} minutes")

    common = sorted(set(A.minutes) & set(B.minutes))
    ca = [A.minutes[m][3] for m in common]
    cb = [B.minutes[m][3] for m in common]
    r_clot = pearson(ca, cb)
    ident = sum(1 for m in common if A.minutes[m] == B.minutes[m])
    ra, rb = [], []
    for i in range(1, len(common)):
        if common[i] - common[i - 1] == 1 and ca[i - 1] and cb[i - 1]:
            ra.append(ca[i] / ca[i - 1] - 1)
            rb.append(cb[i] / cb[i - 1] - 1)
    r_rend = pearson(ra, rb)

    # champs differents (diagnostic pour le texte de la page)
    champ = {"open": 0, "high": 0, "low": 0, "close": 0}
    par_jour: dict[str, int] = {}
    for m in common:
        qa, qb = A.minutes[m], B.minutes[m]
        if qa == qb:
            continue
        d = dt.datetime.fromtimestamp(m * 60, dt.timezone.utc).date().isoformat()
        par_jour[d] = par_jour.get(d, 0) + 1
        for i, k in enumerate(("open", "high", "low", "close")):
            if qa[i] != qb[i]:
                champ[k] += 1
    print(f"minutes communes {fr_int(len(common))} | r clotures {r_clot:.10f} | "
          f"identiques {fr_int(ident)} | r rendements {r_rend:.6f}")
    print("champs differents :", {k: fr_int(v) for k, v in champ.items()})

    os.makedirs(args.out, exist_ok=True)
    fig_correlation(args, common, ca, cb, r_clot, ident, r_rend)
    fig_ecart(args, par_jour, r_clot, B)
    fig_jumeaux(args, A, B)
    return 0


def fig_correlation(args, common, ca, cb, r_clot, ident, r_rend) -> None:
    """Nuage clotures A/B — memes conventions que correlation-1m-bybit.svg."""
    lo = min(min(ca), min(cb))
    hi = max(max(ca), max(cb))
    # bornes arrondies au k$ avec 6 graduations
    lo_k, hi_k = int(lo // 1000), int(-(-hi // 1000))
    x0, x1, y0, y1 = 84.0, 614.0, 596.0, 96.0

    def sx(p):
        return x0 + (p - lo_k * 1000) / (hi_k * 1000 - lo_k * 1000) * (x1 - x0)

    def sy(p):
        return y0 + (p - lo_k * 1000) / (hi_k * 1000 - lo_k * 1000) * (y1 - y0)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 660" '
         f'font-family="system-ui,-apple-system,\'Segoe UI\',sans-serif" role="img" '
         f'aria-label="Corrélation des clôtures 1 minute entre les deux voies chez {args.venue_nom}">',
         f'<rect width="640" height="660" fill="{BG}"/>',
         f'<text x="84" y="34" font-size="19" fill="{BLANC}" text-anchor="start" font-weight="600">'
         f'{fr_int(len(common))} minutes, une seule diagonale</text>',
         f'<text x="84" y="56" font-size="12.5" fill="{SOUS}" text-anchor="start">'
         f'Clôture de chaque minute — voie A (x) contre voie B (y), 1 point sur 12 affiché</text>']
    for i in range(6):
        val = lo_k * 1000 + (hi_k - lo_k) * 1000 * i / 5
        gx, gy = sx(val), sy(val)
        lab = f"{val / 1000:.0f} k$"
        s += [f'<line x1="{gx:.1f}" y1="96" x2="{gx:.1f}" y2="596" stroke="{GRID}"/>',
              f'<line x1="84" y1="{gy:.1f}" x2="614" y2="{gy:.1f}" stroke="{GRID}"/>',
              f'<text x="{gx:.1f}" y="616" font-size="11" fill="{GRIS}" text-anchor="middle">{lab}</text>',
              f'<text x="76" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" text-anchor="end">{lab}</text>']
    s.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="{DIAG}" '
             f'stroke-width="1.5" stroke-dasharray="5 5"/>')
    pts = "".join(f'<circle cx="{sx(a):.1f}" cy="{sy(b):.1f}" r="1.9"/>'
                  for a, b in list(zip(ca, cb))[::12])
    s.append(f'<g fill="{BLEU}" fill-opacity="0.42">{pts}</g>')
    s += [f'<text x="102" y="136" font-size="24" fill="{BLANC}" text-anchor="start" '
          f'font-weight="600">r = {fr_r(r_clot, 8)}</text>',
          f'<text x="102" y="158" font-size="12" fill="{SOUS}" text-anchor="start">'
          f'{fr_int(ident)} / {fr_int(len(common))} chandelles OHLC strictement identiques</text>',
          f'<text x="102" y="176" font-size="12" fill="{SOUS}" text-anchor="start">'
          f'rendements 1 m : r = {fr_r(r_rend, 5)}</text>',
          f'<text x="349.0" y="646" font-size="12" fill="{SOUS}" text-anchor="middle">'
          f'voie A — archives officielles ($)</text>',
          f'<text x="20" y="346.0" font-size="12" fill="{SOUS}" text-anchor="middle" '
          f'transform="rotate(-90 20 346.0)">voie B — {args.b_nature} ($)</text>',
          '</svg>']
    _write(args, f"correlation-1m-{args.venue}.svg", "\n".join(s))


def fig_ecart(args, par_jour: dict[str, int], r_clot: float, B: Voie) -> None:
    """Barres par jour des minutes differentes — gabarit ecart-residuel-bybit.svg."""
    b_legende = ("chandelle reconstruite des ticks (B)" if B.kind == "trades"
                 else "chandelle servie (B)")
    d0 = dt.date.fromisoformat(args.start)
    d1 = dt.date.fromisoformat(args.end)  # exclus
    jours = []
    d = d0
    while d < d1:
        jours.append(d)
        d += dt.timedelta(days=1)
    total = sum(par_jour.values())
    vmax = max(par_jour.values(), default=1)
    pas = max(1, -(-vmax // 4))            # ~4 graduations
    pas = {1: 1, 2: 2, 3: 5, 4: 5}.get(pas, pas)
    if pas > 5:                            # arrondi a un pas « propre »
        base = 10 ** (len(str(pas)) - 1)
        pas = -(-pas // base) * base
    top = -(-vmax // pas) * pas
    y_base, y_top = 366.0, 96.0
    ech = (y_base - y_top) / top

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 920 470" '
         f'font-family="system-ui,-apple-system,\'Segoe UI\',sans-serif" role="img" '
         f'aria-label="Ce qui sépare encore les deux bases {args.venue_nom}, minute par minute">',
         f'<rect width="920" height="470" fill="{BG}"/>',
         f'<text x="76" y="34" font-size="19" fill="{BLANC}" text-anchor="start" '
         f'font-weight="600">L’écart résiduel, minute par minute</text>',
         f'<text x="76" y="56" font-size="12.5" fill="{SOUS}" text-anchor="start">'
         f'Minutes (sur 1 440 par jour) dont la chandelle 1 m diffère entre les deux voies'
         f' — clôtures corrélées à r = {fr_r(r_clot, 8)}</text>',
         f'<text x="68" y="370.0" font-size="11" fill="{GRIS}" text-anchor="end">0</text>']
    v = pas
    while v <= top:
        gy = y_base - v * ech
        s += [f'<line x1="76" y1="{gy:.1f}" x2="896" y2="{gy:.1f}" stroke="{GRID}"/>',
              f'<text x="68" y="{gy + 4:.1f}" font-size="11" fill="{GRIS}" text-anchor="end">{fr_int(v)}</text>']
        v += pas
    for i, d in enumerate(jours):
        n = par_jour.get(d.isoformat(), 0)
        x = 78.8 + i * 20
        h = n * ech
        if h > 3:
            s.append(f'<path d="M{x:.1f} 366.0 v-{h - 3:.1f} q0 -3 3 -3 h8.4 q3 0 3 3 v{h - 3:.1f} z" fill="{GRIS}"/>')
        elif n > 0:
            s.append(f'<rect x="{x:.1f}" y="{366 - h:.1f}" width="14.4" height="{h:.1f}" fill="{GRIS}"/>')
        # etiquette tous les 7 jours depuis le debut + dernier jour (gabarit Bybit)
        if (d - jours[0]).days % 7 == 0 and (jours[-1] - d).days >= 3 or d == jours[-1]:
            s.append(f'<text x="{x + 7.2:.2f}" y="388" font-size="11" fill="{GRIS}" '
                     f'text-anchor="middle">{jour_fr(d)}</text>')
    s += [f'<rect x="76" y="70" width="10" height="10" rx="2" fill="{GRIS}"/>',
          f'<text x="92" y="79" font-size="12" fill="{BLANC}" text-anchor="start">'
          f'chandelle 1 m différente entre trades agrégés (A) et {b_legende}</text>']
    if total == 0:
        # cas OKX : aucune chandelle ne differe — le dire en toutes lettres plutot
        # que de laisser un graphique vide qui semble casse
        s += [f'<text x="486" y="218" font-size="26" fill="{BLANC}" text-anchor="middle" '
              f'font-weight="600">0</text>',
              f'<text x="486" y="246" font-size="14" fill="{SOUS}" text-anchor="middle">'
              f'aucune chandelle ne diffère — les deux voies coïncident exactement, '
              f'minute par minute</text>']
    s.append('</svg>')
    _write(args, f"ecart-residuel-{args.venue}.svg", "\n".join(s))


def fig_jumeaux(args, A: Voie, B: Voie) -> None:
    """Journee temoin : bande high-low + cloture, voie A en haut, voie B en bas."""
    d = dt.date.fromisoformat(args.jumeaux_day)
    m0 = day_ms(args.jumeaux_day) // 60000
    m1 = m0 + 1440
    sa = {m: A.minutes[m] for m in A.minutes if m0 <= m < m1}
    sb = {m: B.minutes[m] for m in B.minutes if m0 <= m < m1}
    lo = min(min(q[2] for q in sa.values()), min(q[2] for q in sb.values()))
    hi = max(max(q[1] for q in sa.values()), max(q[1] for q in sb.values()))
    marge = (hi - lo) * 0.06
    lo, hi = lo - marge, hi + marge
    x0, x1 = 76.0, 896.0

    def sx(m):
        return x0 + (m - m0) / 1440 * (x1 - x0)

    def panel(series, y_top, y_bot, couleur, nom):
        def sy(p):
            return y_bot + (p - lo) / (hi - lo) * (y_top - y_bot)
        ms = sorted(series)
        band_up = " ".join(f"{sx(m):.1f},{sy(series[m][1]):.1f}" for m in ms)
        band_dn = " ".join(f"{sx(m):.1f},{sy(series[m][2]):.1f}" for m in reversed(ms))
        line = " ".join(f"{sx(m):.1f},{sy(series[m][3]):.1f}" for m in ms)
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

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 920 580" '
         f'font-family="system-ui,-apple-system,\'Segoe UI\',sans-serif" role="img" '
         f'aria-label="La même journée {args.venue_nom} par les deux voies, A en haut, B en bas">',
         f'<rect width="920" height="580" fill="{BG}"/>',
         f'<text x="76" y="34" font-size="19" fill="{BLANC}" text-anchor="start" '
         f'font-weight="600">La même journée, reconstruite par les deux voies</text>',
         f'<text x="76" y="56" font-size="12.5" fill="{SOUS}" text-anchor="start">'
         f'BTCUSDT perpétuel {args.venue_nom}, {jour_fr(d)} 2026 (UTC) — bande high-low et '
         f'clôture de chaque minute</text>',
         f'<rect x="76" y="70" width="10" height="10" rx="2" fill="{BLEU}"/>',
         f'<text x="92" y="79" font-size="12" fill="{BLANC}" text-anchor="start">'
         f'voie A — {fr_int(sum(1 for _ in sa))} chandelles agrégées des trades d’archives</text>',
         f'<rect x="440" y="70" width="10" height="10" rx="2" fill="{AQUA}"/>',
         f'<text x="456" y="79" font-size="12" fill="{BLANC}" text-anchor="start">'
         f'voie B — {fr_int(sum(1 for _ in sb))} chandelles '
         f'{"reconstruites" if B.kind == "trades" else "servies"} ({args.b_nature})</text>']
    s += panel(sa, 106.0, 306.0, BLEU, "voie A")
    s += panel(sb, 336.0, 536.0, AQUA, "voie B")
    for hh in (0, 4, 8, 12, 16, 20, 24):
        gx = sx(m0 + hh * 60)
        s.append(f'<text x="{gx:.1f}" y="556" font-size="11" fill="{GRIS}" '
                 f'text-anchor="middle">{hh:02d}:00</text>')
    s.append('</svg>')
    _write(args, f"historiques-jumeaux-{args.venue}.svg", "\n".join(s))


def _grad_prix(lo: float, hi: float) -> list[float]:
    """3-4 graduations « propres » dans [lo, hi]."""
    brut = (hi - lo) / 3
    base = 10 ** (len(str(int(brut))) - 1)
    pas = -(-int(brut) // base) * base
    v = -(-int(lo) // pas) * pas
    out = []
    while v < hi:
        out.append(float(v))
        v += pas
    return out


def _write(args, nom: str, contenu: str) -> None:
    chemin = os.path.join(args.out, nom)
    with open(chemin, "w", encoding="utf-8", newline="\n") as f:
        f.write(contenu + "\n")
    print(f"écrit : {chemin} ({os.path.getsize(chemin) // 1024} Ko)")


if __name__ == "__main__":
    raise SystemExit(main())
