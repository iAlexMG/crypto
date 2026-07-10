"""Figure : la stratégie volume profile (vah_break + filtre delta), TROIS panneaux ALIGNÉS.

  Haut   — le PRIX horaire avec les niveaux du profil de SESSION (VAH / POC / VAL,
           remis à zéro à chaque ouverture de session — les sauts sont réels).
           Les trades sont marqués À LEUR PRIX D'EXÉCUTION RÉEL : ▲ vert = achat,
           ▼ rouge = vente.
  Milieu — l'ANALYSE DES VOLUMES : EMA 24 du delta (volume acheteur - vendeur).
           Le filtre d'entrée exige un delta EMA > 0 (zone verte).
  Bas    — l'ÉQUITÉ. Bandes vertes (identiques partout) = périodes EN POSITION ;
           ailleurs on est À PLAT (cash) donc l'équité est figée.

Les trois panneaux partagent l'axe du temps ET les mêmes marges -> grille alignée.

À lancer depuis la racine du projet, APRÈS le backtest (tout est relu — rien de figé) :
    python formation/01-lean/scripts/fig_volume_profile.py
"""
import csv
import json
from datetime import datetime, timezone, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
VP = "F:/data/ohlcv/BTCUSDT-um/features_vp.csv"
BASE = "backtests/lean/Launcher/bin/Release/"
RESULTAT = BASE + "VolumeProfile.json"
ORDRES = BASE + "VolumeProfile-order-events.json"
OUT = "formation/01-lean/assets/images/lecon-09-volume-profile.png"
DELTA_SPAN = 24
ENCRE, BLEU, ORANGE, VERT, ROUGE, GRIS = "#263238", "#1d4ed8", "#e58e26", "#2e7d32", "#c62828", "#b0bec5"

# ── Prix (CSV source de vérité) — tracé à la CLÔTURE de sa barre
H1 = timedelta(hours=1)
t, close = [], []
with open(CSV) as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        t.append(datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone.utc) + H1)
        close.append(float(row[4]))

# ── Niveaux du profil de session + EMA du delta (features, mêmes valeurs que l'algo)
# Colonnes : time,session,barres,delta,poc,vah,val — les niveaux se REMETTENT À ZÉRO à
# chaque ouverture de session (les sauts des lignes sont réels, pas un artefact).
tv, poc, vah, val, delta_ema = [], [], [], [], []
ema, alpha = None, 2.0 / (DELTA_SPAN + 1)
with open(VP) as f:
    r = csv.reader(f)
    next(r)
    for row in r:
        if row[4] == "":
            continue    # tout début d'historique : lignes que le reader de l'algo saute AUSSI
        d = float(row[3])
        ema = d if ema is None else alpha * d + (1 - alpha) * ema
        tv.append(datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
                  .replace(tzinfo=timezone.utc) + H1)
        poc.append(float(row[4]))
        vah.append(float(row[5]))
        val.append(float(row[6]))
        delta_ema.append(ema)

# ── Trades réels (prix ET instant d'exécution) depuis le journal d'ordres
fills = [e for e in json.load(open(ORDRES)) if e["status"] == "filled"]
def dt(e): return datetime.fromtimestamp(e["time"], tz=timezone.utc)
achats = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "buy"]
ventes = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "sell"]
positions = list(zip([a[0] for a in achats], [v[0] for v in ventes]))

# ── Équité
d = json.load(open(RESULTAT))
vals = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
te = [datetime.fromtimestamp(v[0], tz=timezone.utc) for v in vals]
equite = [v[4] for v in vals]
depart, fin = equite[0], equite[-1]
pic, dd_max = equite[0], 0.0
for e in equite:
    pic = max(pic, e)
    dd_max = max(dd_max, 1 - e / pic)

# ── Tracé : marges FIGÉES -> les trois panneaux ont exactement la même étendue X
fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10.5), sharex=True,
                                    gridspec_kw={"height_ratios": [2.2, 0.8, 1]})
fig.subplots_adjust(left=0.075, right=0.975, top=0.945, bottom=0.06, hspace=0.13)
en_k = FuncFormatter(lambda v, _: f"{v/1000:.0f}k")

for i, (deb, fin_p) in enumerate(positions):            # mêmes bandes sur les TROIS panneaux
    for ax in (ax1, ax2, ax3):
        ax.axvspan(deb, fin_p, color=VERT, alpha=0.12,
                   label="en position (long)" if (i == 0 and ax is ax3) else None)

# -- Haut : prix + niveaux du profil + trades AU PRIX RÉEL
ax1.plot(t, close, color=GRIS, lw=0.8, alpha=0.9, label="Prix BTCUSDT (close 1 h)")
ax1.plot(tv, vah, color=BLEU, lw=0.9, alpha=0.9, label="VAH de la session (haut de value area)")
ax1.plot(tv, poc, color=ORANGE, lw=0.9, alpha=0.9, label="POC de la session (prix le plus traité)")
ax1.plot(tv, val, color=BLEU, lw=0.9, alpha=0.5, ls="--", label="VAL de la session (bas)")
ax1.plot([a[0] for a in achats], [a[1] for a in achats], "^", ms=10, mfc=VERT,
         mec="white", mew=1.2, zorder=6, label=f"achat ▲ ×{len(achats)} (au prix réel)")
ax1.plot([v[0] for v in ventes], [v[1] for v in ventes], "v", ms=10, mfc=ROUGE,
         mec="white", mew=1.2, zorder=6, label=f"vente ▼ ×{len(ventes)} (au prix réel)")
ax1.set_title("Volume profile (cassure de VAH + delta acheteur) — trades à leur prix d'exécution réel",
              fontsize=12.5, weight="bold", color=ENCRE)
ax1.set_ylabel("Prix ($)")
ax1.yaxis.set_major_formatter(en_k)
ax1.grid(alpha=0.25)
ax1.legend(loc="upper right", fontsize=8.5, ncol=2)

# -- Milieu : analyse des volumes (EMA du delta)
ax2.fill_between(tv, delta_ema, 0, where=[x > 0 for x in delta_ema],
                 color=VERT, alpha=0.55, label=f"EMA {DELTA_SPAN} du delta > 0 (acheteurs)")
ax2.fill_between(tv, delta_ema, 0, where=[x <= 0 for x in delta_ema],
                 color=ROUGE, alpha=0.45, label="≤ 0 (vendeurs) → entrées interdites")
ax2.axhline(0, color=ENCRE, lw=0.8)
ax2.set_ylabel("Δ vol. (BTC)")
ax2.grid(alpha=0.25)
ax2.legend(loc="upper right", fontsize=8.5)

# -- Bas : équité
ax3.plot(te, equite, color=ENCRE, lw=1.4, label="Équité ($)")
ax3.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.5)
ax3.plot(te[-1], fin, "o", ms=7, mfc=ENCRE, mec="white", mew=1.3, zorder=6)
ax3.annotate(f"{fin:,.0f} $ ({fin / depart - 1:+.2%})", xy=(te[-1], fin),
             xytext=(-160, 22), textcoords="offset points", fontsize=9.5,
             weight="bold", color=ENCRE,
             arrowprops=dict(arrowstyle="->", lw=1.3, color=ENCRE,
                             connectionstyle="arc3,rad=-0.2"))
ax3.text(0.015, 0.09, f"bandes vertes = EN POSITION · ailleurs À PLAT (cash) → équité figée  ·  "
         f"drawdown max {dd_max:.1%}",
         transform=ax3.transAxes, fontsize=9.5, color=ROUGE, weight="bold")
ax3.set_ylabel("Équité ($)")
ax3.yaxis.set_major_formatter(en_k)
ax3.grid(alpha=0.25)
ax3.legend(loc="upper right", fontsize=9)
ax3.xaxis.set_major_locator(mdates.MonthLocator())
ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  ({len(achats)} achats / {len(ventes)} ventes, "
      f"équité {depart:,.0f} -> {fin:,.0f} $, drawdown max {dd_max:.1%})")
