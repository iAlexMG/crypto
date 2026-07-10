"""Figure : la boucle événementielle de LEAN, en 6 étapes numérotées."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

OUT = "formation/01-lean/assets/images/lecon-02-boucle-evenementielle.png"

GRIS, BLEU, BLEU_F, AMBRE, ENCRE = "#eceff1", "#dbeafe", "#1d4ed8", "#fde68a", "#263238"

fig, ax = plt.subplots(figsize=(12, 6.6))
ax.set_xlim(0, 12); ax.set_ylim(0, 6.6); ax.axis("off")

def zone(x, y, w, h, label, fc, ec, lc):
    ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.10",
                                         fc=fc, ec=ec, lw=1.4))
    ax.text(x + 0.18, y + h - 0.30, label, fontsize=11, weight="bold", color=lc)

def box(x, y, w, h, titre, sous, fc, num=None, bold=False):
    ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.09",
                                         fc=fc, ec=ENCRE, lw=1.3))
    ax.text(x + w/2, y + h*0.63, titre, ha="center", va="center", fontsize=11,
            weight="bold", color=ENCRE, family="monospace")
    ax.text(x + w/2, y + h*0.26, sous, ha="center", va="center", fontsize=8.8, color="#455a64")
    if num:
        ax.add_patch(mpatches.Circle((x, y + h), 0.21, fc=ENCRE, ec="none", zorder=5))
        ax.text(x, y + h, num, ha="center", va="center", fontsize=10.5, color="white",
                weight="bold", zorder=6)

def fleche(x1, y1, x2, y2, label="", color=ENCRE, dx=0.28, lw=2.2, ls="-"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", lw=lw, color=color, linestyle=ls,
                                shrinkA=2, shrinkB=2, mutation_scale=18))
    if label:
        ax.text((x1+x2)/2 + dx, (y1+y2)/2, label, fontsize=9, style="italic",
                color=color, ha="left", va="center")

# --- Zones
zone(0.25, 3.65, 11.5, 2.35, "LE MOTEUR  (Engine/ — celui que tu as compilé)",
     GRIS, "#90a4ae", "#546e7a")
zone(0.25, 0.30, 11.5, 2.35, "TON ALGORITHME  (un fichier .py)", "#f5f9ff", BLEU_F, BLEU_F)

# --- Boîtes moteur
box(1.05, 4.05, 2.6, 1.25, "nouvelle donnée", "tick ou barre, selon la\nResolution souscrite",
    "white", "1")
box(7.10, 4.05, 3.6, 1.25, "FillModel", "décide SEUL le prix et\nle moment du fill",
    AMBRE, "4")

# --- Boîtes algorithme
box(0.75, 0.70, 2.2, 1.25, "initialize()", "dates, cash,\nadd_equity()", "white")
box(3.65, 0.70, 2.6, 1.25, "on_data(slice)", "lire la barre,\ndécider", BLEU, "2", bold=True)
box(7.10, 0.70, 2.2, 1.25, "market_order()", "l'ordre part\nau moteur", BLEU, "3")
box(9.85, 0.70, 1.75, 1.25, "on_order_event()", "le fill revient\nchez toi", BLEU, "5")

# --- Flèches du cycle
fleche(2.35, 4.05, 4.55, 1.95, "  la donnée t\n  (barre clôturée)")       # 1 -> 2
fleche(6.25, 1.32, 7.10, 1.32)                                            # 2 -> 3
fleche(8.20, 1.95, 8.55, 4.05, " ordre soumis")                           # 3 -> 4
fleche(9.95, 4.05, 10.70, 1.95, " OrderEvent\n (fill, frais)", "#b45309")  # 4 -> 5
# 6 : boucle de retour par le haut
ax.plot([8.90, 8.90, 2.35, 2.35], [5.30, 6.28, 6.28, 5.60], color="#546e7a", lw=2.2)
ax.annotate("", xy=(2.35, 5.32), xytext=(2.35, 5.60),
            arrowprops=dict(arrowstyle="-|>", lw=2.2, color="#546e7a", mutation_scale=18))
ax.add_patch(mpatches.Circle((4.3, 6.28), 0.21, fc="#546e7a", ec="none", zorder=5))
ax.text(4.3, 6.28, "6", ha="center", va="center", fontsize=10.5, color="white",
        weight="bold", zorder=6)
ax.text(4.65, 6.50, "barre suivante — et on recommence", fontsize=9.5, style="italic",
        color="#546e7a", va="center")
# initialize : hors boucle
fleche(1.85, 1.95, 1.85, 4.03, " une seule fois,\n au démarrage", "#78909c", ls="--", lw=1.6)

ax.text(6.0, 0.02, "Tu ne choisis pas ton prix d'exécution : entre ③ et ⑤, c'est le "
        "FillModel qui tranche — heures de marché, open disponible, slippage, frais.",
        fontsize=10, style="italic", ha="center", color=ENCRE)
fig.tight_layout()
fig.savefig(OUT, dpi=160, bbox_inches="tight")
print("écrit :", OUT)
