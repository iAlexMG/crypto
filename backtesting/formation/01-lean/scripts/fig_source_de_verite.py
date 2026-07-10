"""Figure : la source de vérité — un seul CSV normalisé nourrit tous les moteurs."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

OUT = "formation/01-lean/assets/images/lecon-02-source-de-verite.png"
ENCRE, GRIS, BLEU, VERT, AMBRE = "#263238", "#eceff1", "#dbeafe", "#dcfce7", "#fef3c7"

fig, ax = plt.subplots(figsize=(12, 4.9))
ax.set_xlim(0, 12); ax.set_ylim(0, 4.9); ax.axis("off")

def box(x, y, w, h, titre, sous, fc, fs=10.5):
    ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.09",
                                         fc=fc, ec=ENCRE, lw=1.3))
    ax.text(x + w/2, y + h*0.64, titre, ha="center", va="center", fontsize=fs,
            weight="bold", color=ENCRE)
    ax.text(x + w/2, y + h*0.27, sous, ha="center", va="center", fontsize=8.6,
            color="#455a64")

def fleche(x1, y1, x2, y2, label="", dy=0.16):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", lw=2.0, color=ENCRE,
                                shrinkA=3, shrinkB=3, mutation_scale=17))
    if label:
        ax.text((x1+x2)/2, (y1+y2)/2 + dy, label, ha="center", fontsize=8.8,
                style="italic", color="#455a64")

box(0.35, 2.7, 2.5, 1.5, "Binance\naggTrades", "chaque transaction :\nprix, taille, agresseur", GRIS)
box(3.55, 2.7, 2.5, 1.5, "SQLite (ticks)", "la donnée BRUTE\n(~10⁸ lignes)", GRIS)
box(6.75, 2.7, 2.6, 1.5, "1H.csv — SOURCE\nDE VÉRITÉ", "OHLCV normalisé :\nUTC, barre = [t, t+1h)", AMBRE)

fleche(2.85, 3.45, 3.55, 3.45, "extraction")
fleche(6.05, 3.45, 6.75, 3.45, "agrégation")

box(6.30, 0.45, 2.5, 1.3, "LEAN", "lecteur PythonData\ncustom", BLEU)
box(9.25, 0.45, 2.5, 1.3, "vectorbt", "même CSV, plus tard\n(section 02)", VERT)
fleche(7.70, 2.7, 7.55, 1.75)
fleche(8.85, 2.7, 10.30, 1.75)

ax.text(9.55, 3.45, "UN seul fichier consommé\npar TOUS les moteurs\n= parité par construction",
        fontsize=10, style="italic", color="#b45309", ha="left", va="center", weight="bold")

ax.text(6.0, 0.02, "Si les deux moteurs lisent le même fichier, tout écart de résultat vient "
        "des moteurs — pas des données. C'est ça, une source de vérité.",
        fontsize=10, style="italic", ha="center", color=ENCRE)
fig.tight_layout()
fig.savefig(OUT, dpi=160, bbox_inches="tight")
print("écrit :", OUT)
