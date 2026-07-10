"""Figure d'ANALYSE : des VP AUX 30 MINUTES sur un canvas PRIX x TEMPS.

Le vrai objectif : voir l'evolution du volume profile en decoupant le temps en briques de
30 min. CHAQUE brique de 30 min = son propre mini-VP : histogramme du volume par niveau de
25 $, dessine DANS son creneau de 30 min (largeur identique pour toutes les briques : chaque
profil est normalise sur LUI-MEME, la barre du POC remplit ~90 % du creneau).
Couleur de chaque barre = RATIO delta / volume total du niveau, dans [-1, +1] :
  +1 = 100 % agresseurs acheteurs (vert), -1 = 100 % vendeurs (rouge), 0 = equilibre (blanc).
Le volume total (perdu par la normalisation) revient en PANNEAU DU BAS : volume par 30 min,
decompose achat (vert) / vente (rouge). Prix horaire en trait ; sessions en pointilles.

Lancer depuis la racine :
    conda run -n backtesting python formation/01-lean/scripts/fig_vp_prix_temps.py
"""
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.ticker import FuncFormatter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backtests.sessions import NY, NOM, bornes_sessions   # définition UNIQUE des sessions

DB = "F:/data/BTCUSDT-um.db"
OUT = "formation/01-lean/assets/images/lecon-09-vp-prix-temps.png"
TICK = 25.0
DEMI_MS = 1_800_000
ENCRE = "#263238"
CMAP = LinearSegmentedColormap.from_list(
    "ratio", ["#b91c1c", "#f2b8b8", "#ffffff", "#b7e2c1", "#15803d"])
VERT, ROUGE = "#16a34a", "#dc2626"
RSAT = 0.5                          # ratio a saturation de couleur (|delta|/vol)
BRIQUE_JOURS = DEMI_MS / 1000 / 86400

DEBUT = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
FIN = DEBUT + timedelta(hours=31)


# --- ticks -> briques de 30 min {niveau: [achat, vente]} + close de brique ---------------
# Le prix trace = le CLOSE de chaque brique de 30 min (dernier tick), issu des MEMES ticks
# que les VP -> aligne exactement, place a la FIN de la brique (instant du close). Tracer un
# close a l'horodatage d'ouverture le decalait d'une brique -> points hors du VP.
d0, d1 = int(DEBUT.timestamp() * 1000), int(FIN.timestamp() * 1000)
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.execute("SELECT ts, price, size, side='buy' FROM trades "
                  "WHERE ts>=? AND ts<? ORDER BY trade_id", (d0, d1))
briques = []                        # (ts_brique_ms, {niveau: [achat, vente]}, close)
tsb, prof, lastp = None, None, None
for ts, prix, taille, achat in cur:
    b = ts // DEMI_MS * DEMI_MS
    if b != tsb:
        if tsb is not None:
            briques.append((tsb, prof, lastp))
        tsb, prof = b, {}
    lvl = round(prix / TICK) * TICK
    cell = prof.setdefault(lvl, [0.0, 0.0])
    cell[0 if achat else 1] += taille
    lastp = prix
if tsb is not None:
    briques.append((tsb, prof, lastp))
con.close()
print(f"{len(briques)} briques de 30 min")

# ligne de prix : close de brique, place a la fin de la brique (t_brique + 30 min)
tprix = [mdates.date2num(datetime.fromtimestamp((tsb + DEMI_MS) / 1000, tz=timezone.utc))
         for tsb, _, _ in briques]
close = [c for _, _, c in briques]

# --- canvas prix x temps + panneau volume ------------------------------------------------
norm = Normalize(-RSAT, RSAT)
fig, (ax, axv) = plt.subplots(2, 1, figsize=(14, 8.4), sharex=True,
                              gridspec_kw={"height_ratios": [3.3, 1], "hspace": 0.05})
fig.subplots_adjust(left=0.062, right=0.93, top=0.92, bottom=0.09)

for tsb, prof, _ in briques:
    x0 = mdates.date2num(datetime.fromtimestamp(tsb / 1000, tz=timezone.utc))
    niveaux = np.array(sorted(prof))
    achat = np.array([prof[l][0] for l in niveaux])
    vente = np.array([prof[l][1] for l in niveaux])
    tot = achat + vente
    ratio = (achat - vente) / tot
    # normalisation PROPRE a la brique -> meme largeur (le POC remplit 90 % du creneau)
    largeur = tot / tot.max() * BRIQUE_JOURS * 0.9
    ax.barh(niveaux, largeur, left=x0, height=TICK, color=CMAP(norm(ratio)), edgecolor="none")
    # panneau volume : achat (vert) empile sous vente (rouge) -> hauteur = volume total 30 min
    xc = x0 + BRIQUE_JOURS / 2
    axv.bar(xc, achat.sum(), width=BRIQUE_JOURS * 0.85, color=VERT, alpha=0.8)
    axv.bar(xc, vente.sum(), width=BRIQUE_JOURS * 0.85, bottom=achat.sum(), color=ROUGE,
            alpha=0.8)

# ouvertures de session (contexte)
for ms, nom in bornes_sessions(d0, d1):
    if d0 <= ms < d1:
        x = mdates.date2num(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))
        for a in (ax, axv):
            a.axvline(x, color=ENCRE, lw=0.8, ls=":", alpha=0.5)
        ax.annotate(NOM[nom], (x, 1.0), xycoords=("data", "axes fraction"),
                    fontsize=8.5, color=ENCRE, ha="left", va="top", alpha=0.8,
                    xytext=(2, -2), textcoords="offset points")

ax.plot(tprix, close, color="#0b2545", lw=1.5, marker=".", ms=4,
        label="Prix BTCUSDT (close 30 min)")
ax.set_title("VP aux 30 min sur prix x temps (largeur = 30 min) — couleur = delta / volume "
             "(intensite d'agression)", fontsize=12.5, weight="bold", color=ENCRE)
ax.set_ylabel("Prix ($)")
ax.set_xlim(mdates.date2num(DEBUT), mdates.date2num(FIN))
ax.legend(loc="lower left", fontsize=9, framealpha=0.9)


# Axe X en HEURE DE NEW YORK : les traits de session tombent sur leurs heures
# locales (NY 09:30, London 03:00, Asia 18:00). Date affichee une seule fois par jour (a
# minuit local + au tout premier tick), heures partout.
def _fmt_temps(x, pos=None):
    dt = mdates.num2date(x, tz=NY)
    if dt.hour == 0 or pos == 0:
        return dt.strftime("%H:%M\n%d %b")
    return dt.strftime("%H:%M")


for a in (ax, axv):
    a.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 2), tz=NY))
    a.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[0, 30], tz=NY))
    a.grid(which="major", axis="x", color=ENCRE, alpha=0.22, lw=0.7)
    a.grid(which="minor", axis="x", color=ENCRE, alpha=0.09, lw=0.5)
    a.grid(which="major", axis="y", alpha=0.2)
ax.tick_params(axis="x", labelbottom=False)
axv.xaxis.set_major_formatter(FuncFormatter(_fmt_temps))
axv.set_xlabel("heure de New York", fontsize=9, color=ENCRE)

axv.set_ylabel("volume / 30 min\n(BTC)", fontsize=9)
axv.bar(np.nan, 0, color=VERT, alpha=0.8, label="achat")
axv.bar(np.nan, 0, color=ROUGE, alpha=0.8, label="vente")
axv.legend(loc="upper left", fontsize=8, ncol=2)

sm = ScalarMappable(norm=norm, cmap=CMAP)
cb = fig.colorbar(sm, ax=(ax, axv), pad=0.01, fraction=0.035, extend="both")
cb.set_ticks([-RSAT, -RSAT / 2, 0, RSAT / 2, RSAT])
cb.set_label("delta / volume total du niveau  (vendeurs  <-  0  ->  acheteurs)", fontsize=9)

fig.savefig(OUT, dpi=150, facecolor="white")
print(f"OK -> {OUT}")
