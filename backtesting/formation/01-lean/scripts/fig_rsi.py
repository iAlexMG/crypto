"""Figure : la stratégie RSI (retour à la moyenne), en trois panneaux ALIGNÉS.

  Haut   — PRIX horaire + trades à leur prix d'exécution réel (▲ achat / ▼ vente),
           bandes vertes = périodes en position.
  Milieu — le RSI(14) lui-même, avec les seuils 30 (survente) et 70 (surachat) et
           les zones ombrées : on ACHÈTE quand il plonge sous 30, on VEND quand il
           franchit 70. C'est là que naissent les ordres.
  Bas    — l'ÉQUITÉ (mêmes bandes de détention).

Marges figées -> mêmes axes X et grille alignés sur les trois panneaux.
À lancer depuis la racine du projet, APRÈS le backtest :
    python formation/01-lean/scripts/fig_rsi.py
"""
import csv
import json
from datetime import datetime, timezone, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

CSV = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
BASE = "backtests/lean/Launcher/bin/Release/"
RESULTAT = BASE + "RsiRetourMoyenne.json"
ORDRES = BASE + "RsiRetourMoyenne-order-events.json"
OUT = "formation/01-lean/assets/images/lecon-05-rsi.png"
N_RSI, SURVENTE, SURACHAT = 14, 30, 70
ENCRE, VIOLET, VERT, ROUGE, GRIS = "#263238", "#6a1b9a", "#2e7d32", "#c62828", "#b0bec5"

# ── Prix + RSI(14) Wilder, recalculés du CSV (mêmes réglages que l'algo) — tracés à
# la CLÔTURE de leur barre (l'instant où close et RSI sont connaissables)
t, close = [], []
with open(CSV) as f:
    r = csv.reader(f); next(r)
    for row in r:
        t.append(datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone.utc) + timedelta(hours=1))
        close.append(float(row[4]))


def rsi_wilder(s, n):
    out = [None] * len(s)
    gains = losses = 0.0
    for i in range(1, len(s)):
        d = s[i] - s[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= n:                       # amorçage : moyenne simple sur n variations
            gains += g; losses += l
            if i == n:
                ag, al = gains / n, losses / n
                out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
        else:                            # lissage de Wilder
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + l) / n
            out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


rsi = rsi_wilder(close, N_RSI)

# ── Trades réels + équité
fills = [e for e in json.load(open(ORDRES)) if e["status"] == "filled"]
def dt(e): return datetime.fromtimestamp(e["time"], tz=timezone.utc)
achats = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "buy"]
ventes = [(dt(e), e["fillPrice"]) for e in fills if e["direction"] == "sell"]
positions = []                            # paires achat->vente (le dernier achat peut rester ouvert)
for k, a in enumerate(achats):
    fin_p = ventes[k][0] if k < len(ventes) else t[-1]
    positions.append((a[0], fin_p))

d = json.load(open(RESULTAT))
vals = d["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
te = [datetime.fromtimestamp(v[0], tz=timezone.utc) for v in vals]
equite = [v[4] for v in vals]
depart, fin = equite[0], equite[-1]
pic, dd_max = equite[0], 0.0
for e in equite:
    pic = max(pic, e); dd_max = max(dd_max, 1 - e / pic)

# ── Tracé : 3 panneaux, marges figées -> étendue X identique
fig, (axP, axR, axE) = plt.subplots(3, 1, figsize=(12, 10), sharex=True,
                                    gridspec_kw={"height_ratios": [2, 1.25, 1.25]})
fig.subplots_adjust(left=0.075, right=0.975, top=0.95, bottom=0.06, hspace=0.12)
en_k = FuncFormatter(lambda v, _: f"{v/1000:.0f}k")

for i, (deb, fin_p) in enumerate(positions):
    for ax in (axP, axE):
        ax.axvspan(deb, fin_p, color=VERT, alpha=0.12,
                   label="en position (long)" if (i == 0 and ax is axE) else None)

# Panneau prix
axP.plot(t, close, color=GRIS, lw=0.8, alpha=0.9, label="Prix BTCUSDT (close 1 h)")
axP.plot([a[0] for a in achats], [a[1] for a in achats], "^", ms=11, mfc=VERT,
         mec="white", mew=1.2, zorder=6, label=f"achat ▲ ×{len(achats)}")
axP.plot([v[0] for v in ventes], [v[1] for v in ventes], "v", ms=11, mfc=ROUGE,
         mec="white", mew=1.2, zorder=6, label=f"vente ▼ ×{len(ventes)}")
axP.set_title(f"RSI {N_RSI} ({SURVENTE}/{SURACHAT}) — retour à la moyenne : acheter la survente, vendre le surachat",
              fontsize=12.5, weight="bold", color=ENCRE)
axP.set_ylabel("Prix ($)"); axP.yaxis.set_major_formatter(en_k)
axP.grid(alpha=0.25); axP.legend(loc="upper right", fontsize=8.5, ncol=3)

# Panneau RSI
axR.axhspan(SURACHAT, 100, color=ROUGE, alpha=0.08)
axR.axhspan(0, SURVENTE, color=VERT, alpha=0.08)
axR.plot(t, rsi, color=VIOLET, lw=1.1)
axR.axhline(SURACHAT, color=ROUGE, lw=1.0, ls="--", alpha=0.7)
axR.axhline(SURVENTE, color=VERT, lw=1.0, ls="--", alpha=0.7)
axR.text(t[5], SURACHAT + 2, f"surachat {SURACHAT} → vente", fontsize=8.5, color=ROUGE, va="bottom")
axR.text(t[5], SURVENTE - 2, f"survente {SURVENTE} → achat", fontsize=8.5, color=VERT, va="top")
axR.set_ylabel(f"RSI {N_RSI}"); axR.set_ylim(0, 100); axR.set_yticks([0, 30, 50, 70, 100])
axR.grid(alpha=0.25)

# Panneau équité
axE.plot(te, equite, color=ENCRE, lw=1.4, label="Équité ($)")
axE.axhline(depart, color=ENCRE, lw=1.0, ls="--", alpha=0.5)
axE.plot(te[-1], fin, "o", ms=7, mfc=ENCRE, mec="white", mew=1.3, zorder=6)
axE.annotate(f"{fin:,.0f} $ ({fin / depart - 1:+.2%})", xy=(te[-1], fin),
             xytext=(-150, 20), textcoords="offset points", fontsize=9.5,
             weight="bold", color=ENCRE,
             arrowprops=dict(arrowstyle="->", lw=1.3, color=ENCRE, connectionstyle="arc3,rad=0.2"))
axE.text(0.015, 0.10, f"drawdown max {dd_max:.1%} — acheter la faiblesse en marché baissier = "
         f"tenir des couteaux qui tombent", transform=axE.transAxes, fontsize=9.5,
         color=ROUGE, weight="bold")
axE.set_ylabel("Équité ($)"); axE.yaxis.set_major_formatter(en_k)
axE.grid(alpha=0.25); axE.legend(loc="upper right", fontsize=9)
axE.xaxis.set_major_locator(mdates.MonthLocator())
axE.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

fig.savefig(OUT, dpi=150)
print(f"OK -> {OUT}  (RSI check: 1er achat vers RSI~27 ; {len(achats)} achats / "
      f"{len(ventes)} ventes ; équité {depart:,.0f} -> {fin:,.0f} $, dd max {dd_max:.1%})")
