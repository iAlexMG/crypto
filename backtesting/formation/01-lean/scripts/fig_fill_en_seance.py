"""Figure : la RÈGLE GÉNÉRALE du fill en séance, sur les vraies barres minute."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

OUT = "formation/01-lean/assets/images/lecon-02-fill-en-seance.png"

# Les 4 barres minute RÉELLES du journal (AnatomieMinute-log.txt), étiquetées par leur CLÔTURE
LABELS = ["09:59", "10:00", "10:01", "10:02"]
O = [145.0411, 145.0498, 145.0152, 145.0325]
H = [145.0843, 145.0584, 145.0411, 145.0325]
L = [145.0238, 145.0152, 145.0065, 144.9287]
C = [145.0498, 145.0152, 145.0325, 144.9546]

VERT, ROUGE, ENCRE, AMBRE, BLEU = "#2e7d32", "#c62828", "#263238", "#b45309", "#1d4ed8"

fig, ax = plt.subplots(figsize=(11, 5.8))
W = 0.28
for i in range(4):
    couleur = VERT if C[i] >= O[i] else ROUGE
    ax.plot([i, i], [L[i], H[i]], color=ENCRE, lw=1.4, zorder=2)          # mèche
    ax.add_patch(mpatches.Rectangle((i - W/2, min(O[i], C[i])), W,
                                    abs(C[i] - O[i]) or 0.002,
                                    fc=couleur, ec=ENCRE, lw=1.0, zorder=3))

# Le point unique : décision ET fill, même instant, même prix
x_fill, y_fill = 1 + W/2 + 0.10, C[1]
ax.plot(x_fill, y_fill, "o", ms=13, mfc=AMBRE, mec=BLEU, mew=2.5, zorder=6)

ax.annotate("(1) 10:00:00 — on_data voit la barre close\n"
            "    close = 145.0152 $ -> market_order(SPY, 100)",
            xy=(x_fill, y_fill), xytext=(-0.45, 145.115), fontsize=10,
            family="monospace", color=BLEU,
            arrowprops=dict(arrowstyle="->", lw=1.8, color=BLEU,
                            connectionstyle="arc3,rad=-0.2"))

ax.annotate("(2) 10:00:00 — FILL IMMÉDIAT, même seconde\n"
            "    FillPrice 145.0152 $ = le close observé\n"
            "    (+ 1 $ de frais, modèle IB)",
            xy=(x_fill, y_fill), xytext=(1.75, 145.075), fontsize=10,
            family="monospace", color=AMBRE, weight="bold",
            arrowprops=dict(arrowstyle="->", lw=1.8, color=AMBRE,
                            connectionstyle="arc3,rad=0.25"))

ax.text(1.75, 144.965,
        "convention « au dernier prix connu » — légèrement optimiste :\n"
        "en réel tu obtiendrais le tick suivant (spread, latence)",
        fontsize=9, style="italic", color="#78909c")

ax.set_xticks(range(4)); ax.set_xticklabels(LABELS, fontsize=10)
ax.set_xlabel("clôture de la barre minute (heure de New York)", fontsize=10)
ax.set_ylabel("SPY ($, ajusté)", fontsize=10)
ax.set_xlim(-0.7, 3.7); ax.set_ylim(144.90, 145.15)
ax.grid(axis="y", alpha=0.25)
for cote in ("top", "right"):
    ax.spines[cote].set_visible(False)
ax.set_title("En séance, un ordre au marché est rempli immédiatement — au dernier prix connu",
             fontsize=12, weight="bold", pad=12)
fig.tight_layout()
fig.savefig(OUT, dpi=160, bbox_inches="tight")
print("écrit :", OUT)
