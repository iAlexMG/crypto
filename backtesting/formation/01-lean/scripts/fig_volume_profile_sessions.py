"""Figure concept : le volume profile PAR SESSION et son ÉVOLUTION intra-session.

Zoom sur ~2 jours : le prix horaire, les bandes de session (Asia / London / NY — la
définition UNIQUE vit dans backtests/sessions.py, les étiquettes de légende en sont
DÉRIVÉES : elles ne peuvent plus contredire les bandes), et pour chaque session ses
niveaux VAH / POC / VAL qui se DÉVELOPPENT barre après barre (les « sous-VP » horaires) :
profil remis à zéro à l'ouverture, nerveux sur les premières barres, qui se stabilise à
mesure que l'enchère se construit. Hors session (17:00-17:59 NY) : niveaux GELÉS — le
dernier niveau connu se prolonge à travers la zone grise.

Convention temporelle : close ET niveaux sont tracés à la CLÔTURE de leur barre —
l'instant où ils deviennent connaissables. Axe en heure de New York : les frontières de
session tombent sur 03:00 / 09:30 / 17:00 / 18:00.

À lancer depuis la racine du projet, APRÈS le générateur de features :
    python formation/01-lean/scripts/fig_volume_profile_sessions.py
"""
import csv
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backtests.sessions import NY, NOM, plages_ny

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
VP = "F:/data/ohlcv/BTCUSDT-um/features_vp.csv"
OUT = "formation/01-lean/assets/images/lecon-09-volume-profile-sessions.png"
DEBUT = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)   # ~2 jours de zoom
FIN = DEBUT + timedelta(hours=48)
H1 = timedelta(hours=1)
ENCRE, BLEU, ORANGE, GRIS = "#263238", "#1d4ed8", "#e58e26", "#b0bec5"
COULEUR_SESSION = {"asia": "#7c3aed", "london": "#0891b2", "ny": "#16a34a", "hors": "#9ca3af"}


def lire(chemin, filtre):
    with open(chemin) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            t = datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if filtre(t):
                yield t, row


t, close = [], []
for ts, row in lire(CSV, lambda x: DEBUT <= x < FIN):
    t.append(ts + H1)                      # le close n'existe qu'à la CLÔTURE de la barre
    close.append(float(row[4]))

feats = [(ts, row[1], float(row[4]), float(row[5]), float(row[6]))
         for ts, row in lire(VP, lambda x: DEBUT <= x < FIN) if row[4] != ""]

fig, ax = plt.subplots(figsize=(12, 6.4))
fig.subplots_adjust(left=0.075, right=0.975, top=0.9, bottom=0.13)

# Bandes de session (une barre = la session active à SA CLÔTURE) ; étiquettes DÉRIVÉES
# de backtests/sessions.py — la légende ne peut pas contredire le découpage réel.
plages = plages_ny()
vus = set()
for ts, session, *_ in feats:
    label = None
    if session not in vus:
        vus.add(session)
        label = ("hors session (niveaux gelés)" if session == "hors"
                 else f"session {NOM[session]} ({plages[session]})")
    ax.axvspan(ts, ts + H1, color=COULEUR_SESSION[session],
               alpha=0.10 if session != "hors" else 0.18, label=label)

# Niveaux : une POLYLIGNE PAR SESSION (pas de trait à travers les resets), tracés à la
# CLÔTURE. Un segment « hors » reprend le dernier point de la session précédente, pour
# que les niveaux GELÉS restent visibles à travers la zone grise.
segments, cur = [], []
for ts, session, poc, vah, val in feats:
    if cur and session != cur[-1][1]:
        precedent = cur[-1]
        segments.append(cur)
        cur = [(precedent[0], session, *precedent[2:])] if session == "hors" else []
    cur.append((ts + H1, session, poc, vah, val))
if cur:
    segments.append(cur)
for i, seg in enumerate(segments):
    x = [p[0] for p in seg]
    ax.plot(x, [p[3] for p in seg], color=BLEU, lw=1.6,
            label="VAH (se développe barre à barre)" if i == 0 else None)
    ax.plot(x, [p[2] for p in seg], color=ORANGE, lw=1.6,
            label="POC (se développe barre à barre)" if i == 0 else None)
    ax.plot(x, [p[4] for p in seg], color=BLEU, lw=1.4, ls="--", alpha=0.7,
            label="VAL" if i == 0 else None)

ax.plot(t, close, color=ENCRE, lw=1.3, marker=".", ms=4, label="Prix BTCUSDT (close 1 h)")
ax.set_title("Le profil de CHAQUE session se construit barre après barre (sous-VP horaires)",
             fontsize=12.5, weight="bold", color=ENCRE)
ax.set_ylabel("Prix ($)")
ax.set_xlabel("heure de New York", fontsize=9, color=ENCRE)
ax.grid(alpha=0.25)
ax.legend(loc="best", fontsize=8.5, ncol=2)


# Axe X en heure de NEW YORK : les frontières de session tombent sur leurs heures
# locales (London 03:00, NY 09:30, hors 17:00, Asia 18:00). Date affichée à minuit
# local et au tout premier tick.
def _fmt_temps(x, pos=None):
    dt = mdates.num2date(x, tz=NY)
    if dt.hour == 0 or pos == 0:
        return dt.strftime("%H:%M\n%d %b")
    return dt.strftime("%H:%M")


ax.xaxis.set_major_locator(mdates.HourLocator(byhour=(0, 6, 12, 18), tz=NY))
ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1, tz=NY))
ax.xaxis.set_major_formatter(FuncFormatter(_fmt_temps))

fig.savefig(OUT, dpi=150, facecolor="white")
print(f"OK -> {OUT}  ({len(segments)} segments de session sur la fenêtre)")
