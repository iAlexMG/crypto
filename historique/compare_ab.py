#!/usr/bin/env python3
r"""Validation croisee voie A / voie B — gabarit REUTILISABLE (stdlib seule).

Compare deux bases du pilier (schema commun `trades(trade_id, ts, price, size,
side)`) sur une fenetre UTC : agregats par jour (comptes, buy/sell, volume,
high/low) puis chandelles 1 minute (agregation streaming ORDER BY trade_id,
fetchmany 500 k — l'hypothese rowid = chrono des deux voies) : minutes communes,
correlation des clotures, chandelles OHLC identiques, correlation des rendements.
Ce sont les chiffres des figures des pages hist-* (gabarit : la page hist-bybit).

Pourquoi un script conserve : la comparaison Bybit avait ete faite par scripts
ephemeres (seules les figures restent) — a la troisieme venue, le gabarit merite
d'etre versionne. Il resert tel quel pour OKX, KuCoin, Bitget…

Pieges couverts :
  - unites voie B : certaines venues servent des CONTRATS via Quantower (OKX
    ctVal 0,01 ; KuCoin multiplier 0,001) -> --mult-b applique un facteur aux
    sizes de B ; le ratio des volumes A/B par jour (affiche) DIT s'il en faut un ;
  - voie B potentiellement AGREGEE (constate sur OKX : ~2,6x moins de lignes que
    de trades) -> les comptes ne sont PAS un critere d'echec, les volumes,
    extremes et clotures 1 m le sont ;
  - voie B en BARRES 1 m (constate sur KuCoin : aucun historique de trades via
    Quantower, seules les chandelles remontent -> CryptoBarsExtractorStrategy,
    base <sym>-<exchange>-<marche>-qt1m.db, table `bars`) -> detection AUTO du
    schema : les chandelles B sont lues telles quelles au lieu d'etre agregees ;
    comptes et buy/sell n'existent pas dans une barre, criteres exclus ;
  - base B en cours d'ecriture par Quantower -> ouverture sqlite en LECTURE SEULE
    (mode=ro) et fenetre bornee a la couverture commune (--auto-clip).

EXEMPLES :
  # Bybit, fenetre du site (reproduit les chiffres publies de hist-bybit)
  python compare_ab.py --a data\BTCUSDT-bybit-perp-api.db --b data\BTCUSDT-bybit-perp-qt.db ^
      --start 2026-06-01 --end 2026-07-12

  # OKX, jour temoin, bornage automatique a la couverture commune
  python compare_ab.py --a data\BTCUSDT-okx-perp-api.db --b data\BTCUSDT-okx-perp-qt.db ^
      --start 2026-07-12 --end 2026-07-13 --auto-clip

  # KuCoin, voie B en CHANDELLES 1 m (aucun historique de trades via Quantower ;
  # volume en contrats -> multiplier 0,001 mesure sur /api/v1/contracts/XBTUSDTM)
  python compare_ab.py --a data\BTCUSDT-kucoin-perp-api.db --b data\BTCUSDT-kucoin-perp-qt1m.db ^
      --start 2026-06-01 --end 2026-07-12 --mult-b 0.001
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sqlite3

FETCH = 500_000  # lignes par fetchmany (streaming, ~5 min par base de 100 M)


def connect_ro(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise SystemExit(f"Base introuvable : {path}")
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def day_ms(s: str) -> int:
    d = dt.date.fromisoformat(s)
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


class Voie:
    """Agregation streaming d'une base sur [t0, t1) : jours + chandelles 1 m.

    Deux schemas acceptes, detectes automatiquement : `trades` (agrege ici en 1 m)
    ou `bars` (chandelles 1 m deja faites — CryptoBarsExtractorStrategy, cas KuCoin).
    """

    def __init__(self, name: str, path: str, mult: float) -> None:
        self.name = name
        self.path = path
        self.mult = mult
        self.days: dict[str, dict] = {}       # 'AAAA-MM-JJ' -> agregats
        self.minutes: dict[int, list] = {}    # ts//60000 -> [o, h, l, c]
        self.rows = 0
        conn = connect_ro(path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        if "trades" in tables:
            self.kind = "trades"
        elif "bars" in tables:
            self.kind = "bars"
        else:
            raise SystemExit(f"{path} : ni table trades ni table bars.")

    def scan(self, t0: int, t1: int) -> None:
        if self.kind == "bars":
            self._scan_bars(t0, t1)
            return
        conn = connect_ro(self.path)
        cur = conn.execute(
            "SELECT ts, price, size, side FROM trades WHERE ts>=? AND ts<? ORDER BY trade_id",
            (t0, t1))
        days, minutes = self.days, self.minutes
        while True:
            chunk = cur.fetchmany(FETCH)
            if not chunk:
                break
            self.rows += len(chunk)
            for ts, price, size, side in chunk:
                size *= self.mult
                d = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date().isoformat()
                a = days.get(d)
                if a is None:
                    a = days[d] = {"n": 0, "buy": 0, "sell": 0, "vol": 0.0,
                                   "hi": price, "lo": price}
                a["n"] += 1
                a[side] = a.get(side, 0) + 1
                a["vol"] += size
                if price > a["hi"]:
                    a["hi"] = price
                if price < a["lo"]:
                    a["lo"] = price
                m = ts // 60000
                c = minutes.get(m)
                if c is None:
                    minutes[m] = [price, price, price, price]
                else:
                    if price > c[1]:
                        c[1] = price
                    if price < c[2]:
                        c[2] = price
                    c[3] = price  # ORDER BY trade_id -> derniere = cloture
        conn.close()

    def _scan_bars(self, t0: int, t1: int) -> None:
        """Chandelles deja faites : lecture directe, volume x mult, extremes des jours
        depuis high/low. Pas de comptes de trades ni de buy/sell dans une barre."""
        conn = connect_ro(self.path)
        cur = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM bars "
            "WHERE ts>=? AND ts<? ORDER BY ts", (t0, t1))
        days, minutes = self.days, self.minutes
        while True:
            chunk = cur.fetchmany(FETCH)
            if not chunk:
                break
            self.rows += len(chunk)
            for ts, o, h, l, c, v in chunk:
                v *= self.mult
                d = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).date().isoformat()
                a = days.get(d)
                if a is None:
                    a = days[d] = {"n": 0, "vol": 0.0, "hi": h, "lo": l}
                a["n"] += 1
                a["vol"] += v
                if h > a["hi"]:
                    a["hi"] = h
                if l < a["lo"]:
                    a["lo"] = l
                minutes[ts // 60000] = [o, h, l, c]
        conn.close()

    def coverage(self) -> tuple[int, int] | None:
        conn = connect_ro(self.path)
        r = conn.execute(f"SELECT min(ts), max(ts) FROM {self.kind}").fetchone()
        conn.close()
        if not r or r[0] is None:
            return None
        # une barre d'ouverture ts couvre [ts, ts+60000) : la couverture reelle
        # s'etend jusqu'a la FIN de la derniere barre
        return (r[0], r[1] + 59_999) if self.kind == "bars" else (r[0], r[1])


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for x, y in zip(xs, ys):
        dx, dy = x - mx, y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")


def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def main() -> int:
    p = argparse.ArgumentParser(description="Validation croisee voie A / voie B.")
    p.add_argument("--a", required=True, help="base voie A (reference)")
    p.add_argument("--b", required=True, help="base voie B (a valider)")
    p.add_argument("--start", required=True, help="debut fenetre UTC (YYYY-MM-DD, inclus)")
    p.add_argument("--end", required=True, help="fin fenetre UTC (YYYY-MM-DD, EXCLUS)")
    p.add_argument("--mult-b", type=float, default=1.0,
                   help="facteur applique aux sizes de B (contrats -> actif de base)")
    p.add_argument("--auto-clip", action="store_true",
                   help="borner la fenetre a la couverture commune des deux bases "
                        "(base B en cours de collecte)")
    args = p.parse_args()

    t0, t1 = day_ms(args.start), day_ms(args.end)
    A = Voie("A", args.a, 1.0)
    B = Voie("B", args.b, args.mult_b)

    if args.auto_clip:
        ca, cb = A.coverage(), B.coverage()
        if not ca or not cb:
            raise SystemExit("Une des bases est vide.")
        lo = max(t0, ca[0], cb[0])
        hi = min(t1, ca[1] + 1, cb[1] + 1)
        # bornes sur minutes ENTIERES : une minute tronquee d'un cote fausserait
        # cloture/extremes de la minute commune
        lo = ((lo + 59_999) // 60000) * 60000
        hi = (hi // 60000) * 60000
        if hi <= lo:
            raise SystemExit("Aucune couverture commune dans la fenetre demandee.")
        print(f"Fenetre effective (clip couverture commune) : {iso(lo)} -> {iso(hi)}")
        t0, t1 = lo, hi
    else:
        print(f"Fenetre : {iso(t0)} -> {iso(t1)}")

    for v in (A, B):
        print(f"Voie {v.name} : {v.path}"
              + ("  [chandelles 1 m deja faites]" if v.kind == "bars" else "")
              + (f"  (sizes x{v.mult})" if v.mult != 1 else ""))
        t = dt.datetime.now()
        v.scan(t0, t1)
        el = (dt.datetime.now() - t).total_seconds()
        print(f"  {human(v.rows)} lignes lues en {el:.0f}s, {len(v.minutes)} minutes")

    # --- agregats par jour ------------------------------------------------- #
    print("\n== Par jour (A | B) ==")
    print(f"{'jour':<12}{'lignes A':>12}{'lignes B':>12}{'vol A':>12}{'vol B':>12}"
          f"{'B/A vol':>9}{'hi A':>10}{'hi B':>10}{'lo A':>10}{'lo B':>10}")
    for d in sorted(set(A.days) | set(B.days)):
        a, b = A.days.get(d), B.days.get(d)
        if a and b:
            ratio = f"{b['vol'] / a['vol']:.4f}" if a["vol"] else "-"
            print(f"{d:<12}{human(a['n']):>12}{human(b['n']):>12}"
                  f"{a['vol']:>12,.1f}{b['vol']:>12,.1f}{ratio:>9}"
                  f"{a['hi']:>10,.1f}{b['hi']:>10,.1f}{a['lo']:>10,.1f}{b['lo']:>10,.1f}")
        else:
            print(f"{d:<12}  present dans {'A' if a else 'B'} seulement "
                  f"({human((a or b)['n'])} lignes)")
    na = sum(a["n"] for a in A.days.values())
    nb = sum(b["n"] for b in B.days.values())
    va = sum(a["vol"] for a in A.days.values())
    vb = sum(b["vol"] for b in B.days.values())
    print(f"{'TOTAL':<12}{human(na):>12}{human(nb):>12}{va:>12,.1f}{vb:>12,.1f}"
          f"{(vb / va if va else float('nan')):>9.4f}")
    def buy_txt(v, n):
        if v.kind == "bars":
            return "n/d (barres)"
        t = sum(a.get("buy", 0) for a in v.days.values())
        return f"{human(t)} ({100 * t / n:.2f} %)" if n else "-"
    print(f"buy A {buy_txt(A, na)} | buy B {buy_txt(B, nb)}")

    # --- chandelles 1 minute ------------------------------------------------ #
    common = sorted(set(A.minutes) & set(B.minutes))
    only_a = len(A.minutes) - len(common)
    only_b = len(B.minutes) - len(common)
    print(f"\n== Chandelles 1 minute ==")
    print(f"minutes communes : {human(len(common))} "
          f"(A seul : {only_a}, B seul : {only_b})")
    if len(common) < 3:
        print("Trop peu de minutes communes pour les correlations.")
        return 0
    ca = [A.minutes[m][3] for m in common]
    cb = [B.minutes[m][3] for m in common]
    print(f"correlation clotures : r = {pearson(ca, cb):.10f}")
    ident = sum(1 for m in common if A.minutes[m] == B.minutes[m])
    print(f"chandelles OHLC identiques : {human(ident)} / {human(len(common))} "
          f"({100 * ident / len(common):.2f} %)")
    # rendements minute a minute (sur minutes communes CONSECUTIVES)
    ra, rb = [], []
    for i in range(1, len(common)):
        if common[i] - common[i - 1] == 1 and ca[i - 1] and cb[i - 1]:
            ra.append(ca[i] / ca[i - 1] - 1)
            rb.append(cb[i] / cb[i - 1] - 1)
    if len(ra) >= 3:
        print(f"correlation rendements 1 m : r = {pearson(ra, rb):.6f} "
              f"({human(len(ra))} paires consecutives)")
    # ecart des clotures (matiere de la figure ecart-residuel)
    diffs = [abs(x - y) for x, y in zip(ca, cb)]
    rel = [d / x for d, x in zip(diffs, ca) if x]
    print(f"ecart clotures : max {max(diffs):.1f} | moyen {sum(diffs) / len(diffs):.3f} "
          f"| relatif moyen {100 * sum(rel) / len(rel):.5f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
