"""Diagnostic (leçon 09, coda) : les descripteurs d'order-flow fin séparent-ils les issues ?

On teste, SANS backtest LEAN, deux descripteurs tirés des VP 30 min sur la MEME issue que
la stratégie (cassure de VAH -> cible avant stop) :
  1. DELTA AU NIVEAU FRANCHI (ratio_vah = delta/volume au niveau VAH du profil de session) :
     une cassure « portée » par des acheteurs À CE NIVEAU gagne-t-elle plus ?
  2. VELOCITE DU POC (migration du POC sur LAG barres de la session) : le POC qui monte
     (valeur acceptée plus haut) prédit-il la continuation ?

Miroir de la stratégie : profil de session développé (Asia 18:00, heure NY), événement =
close franchit vah par le bas en session (barres>=3, edge>=0.2%), issue = cible
vah+(vah-poc) atteinte AVANT stop vah-0.5*(vah-poc) sur les closes 1H.

    python formation/01-lean/scripts/diag_descripteurs_vp.py
"""
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backtests.sessions import bornes_sessions   # définition UNIQUE des sessions (heure NY)

DB = "F:/data/BTCUSDT-um.db"
CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
TICK = 25.0
DEMI_MS = 1_800_000
HEURE_MS = 3_600_000
MIN_EDGE = 0.002
MIN_BARRES = 3
LAG = 3                 # fenêtre de migration du POC (barres de session)


def value_area(profil, couverture=0.70):
    totaux = {p: a + v for p, (a, v) in profil.items()}
    total = sum(totaux.values())
    poc = max(totaux, key=totaux.get)
    niveaux = sorted(totaux)
    lo = hi = niveaux.index(poc)
    acquis = totaux[poc]
    while acquis < couverture * total and (lo > 0 or hi < len(niveaux) - 1):
        haut = totaux[niveaux[hi + 1]] if hi < len(niveaux) - 1 else -1.0
        bas = totaux[niveaux[lo - 1]] if lo > 0 else -1.0
        if haut >= bas:
            hi += 1
            acquis += totaux[niveaux[hi]]
        else:
            lo -= 1
            acquis += totaux[niveaux[lo]]
    return poc, niveaux[hi], niveaux[lo]


close_par_t = {}
with open(CSV) as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        close_par_t[row[0][:19]] = float(row[4])

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
tmin, tmax = con.execute("SELECT MIN(ts), MAX(ts) FROM trades").fetchone()
bornes = bornes_sessions(tmin, tmax)
cur = con.execute("SELECT ts, price, size, side='buy' FROM trades ORDER BY trade_id")

bars = []           # (t_str, session, barres, poc, vah, val, ratio_vah)
i, session, run = 0, "hors", {}
geles = (None, None, None, None)
barres = 0
tsb, prof = None, None


def ratio(cellule):
    a, v = cellule
    tot = a + v
    return (a - v) / tot if tot else 0.0


def flush(tsb, prof):
    global i, session, run, geles, barres
    while i < len(bornes) and bornes[i][0] <= tsb:
        if bornes[i][1] != session:
            session = bornes[i][1]
            barres = 0
            if session != "hors":
                run = {}
        i += 1
    if session != "hors":
        for niveau, (a, v) in prof.items():
            cell = run.setdefault(niveau, [0.0, 0.0])
            cell[0] += a
            cell[1] += v
    if tsb % HEURE_MS == 0:
        return
    t = datetime.fromtimestamp((tsb - DEMI_MS) / 1000, tz=timezone.utc)
    if session != "hors" and run:
        barres += 1
        poc, vah, val = value_area(run)
        geles = (poc, vah, val, ratio(run[vah]))
    poc, vah, val, rvah = geles
    bars.append((f"{t:%Y-%m-%d %H:%M:%S}", session, barres, poc, vah, val, rvah))


for ts, prix, taille, achat in cur:
    b = ts // DEMI_MS * DEMI_MS
    if b != tsb:
        if tsb is not None:
            flush(tsb, prof)
        tsb, prof = b, {}
    lvl = round(prix / TICK) * TICK
    cell = prof.setdefault(lvl, [0.0, 0.0])
    cell[0 if achat else 1] += taille
if tsb is not None:
    flush(tsb, prof)
con.close()

# --- événements de cassure de VAH + issue + les deux descripteurs ------------------------
closes = [close_par_t.get(b[0]) for b in bars]
evenements = []      # (ratio_vah, vel_poc | None, win, rendement)
close_prec, vah_prec = None, None
for k, (t, sess, brs, poc, vah, val, rvah) in enumerate(bars):
    c = closes[k]
    if c is None or vah is None:
        close_prec, vah_prec = c, vah
        continue
    if (close_prec is not None and vah_prec is not None
            and close_prec <= vah_prec and c > vah
            and sess != "hors" and brs >= MIN_BARRES):
        cible = vah + (vah - poc)
        stop = vah - 0.5 * (vah - poc)
        if (cible - vah) / vah >= MIN_EDGE:
            issue = None
            for j in range(k + 1, len(bars)):
                cj = closes[j]
                if cj is None:
                    continue
                if cj >= cible:
                    issue = (True, cible / c - 1)
                    break
                if cj <= stop:
                    issue = (False, stop / c - 1)
                    break
            if issue is not None:
                vel = None
                if k >= LAG and bars[k - LAG][1] == sess and bars[k - LAG][3] is not None:
                    vel = (poc - bars[k - LAG][3]) / poc
                evenements.append((rvah, vel, issue[0], issue[1]))
    close_prec, vah_prec = c, vah


# Sortie compacte — c'est CE format exact qui est cité dans la leçon 09 (coda).
COL = 33                # colonne du « : » (alignement des bilans)


def bilan(nom, sous, indent="  "):
    n = len(sous)
    if not n:
        print(f"{indent}{nom:<{COL - len(indent)}}: aucun événement")
        return
    wr = sum(1 for e in sous if e[-2]) / n
    esp = sum(e[-1] for e in sous) / n
    print(f"{indent}{nom:<{COL - len(indent)}}: n={n:>3}  win {100 * wr:.1f}%  "
          f"espérance {100 * esp:+.3f}%")


def quartiles(nom, sous, cle):
    tries = sorted(sous, key=cle)
    q = len(tries) // 4
    parts = [tries[:q], tries[q:2 * q], tries[2 * q:3 * q], tries[3 * q:]]
    vals = " · ".join(f"Q{i + 1} {100 * sum(e[-1] for e in p) / len(p):+.3f}%"
                      for i, p in enumerate(parts))
    print(f"  {nom:<{COL - 2}}: {vals}")


print(f"{len(evenements)} cassures de VAH avec issue résolue\n")
print("1) DELTA AU NIVEAU VAH (ratio_vah)")
bilan("cassure absorbée (r<=0)", [e for e in evenements if e[0] <= 0])
bilan("cassure portée (r>0)", [e for e in evenements if e[0] > 0])
quartiles("quartiles (rouge->vert)", evenements, lambda e: e[0])
ev_vel = [e for e in evenements if e[1] is not None]
print(f"\n2) VÉLOCITÉ DU POC ({LAG} barres, n={len(ev_vel)})")
bilan("POC descend (vel<=0)", [e for e in ev_vel if e[1] <= 0])
bilan("POC monte (vel>0)", [e for e in ev_vel if e[1] > 0])
quartiles("quartiles (baissier->haussier)", ev_vel, lambda e: e[1])
print()
bilan("TOUTES (référence)", evenements, indent="")
