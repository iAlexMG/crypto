"""Figure : le contrat d'une barre — [t, t+1h), time=ouverture, end_time=clôture."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

OUT = "formation/01-lean/assets/images/lecon-02-contrat-barre.png"
ENCRE, BLEU, AMBRE, ROUGE = "#263238", "#1d4ed8", "#b45309", "#c62828"

fig, ax = plt.subplots(figsize=(11, 4.6))
ax.set_xlim(0, 11); ax.set_ylim(0, 4.6); ax.axis("off")

# Axe du temps
ax.annotate("", xy=(10.6, 1.6), xytext=(0.4, 1.6),
            arrowprops=dict(arrowstyle="-|>", lw=2.2, color=ENCRE, mutation_scale=18))
for x, t in [(1.5, "00:00"), (4.5, "01:00"), (7.5, "02:00")]:
    ax.plot([x, x], [1.45, 1.75], color=ENCRE, lw=2.2)
    ax.text(x, 1.10, f"{t} UTC", ha="center", fontsize=10.5, weight="bold", color=ENCRE)

# La barre [00:00, 01:00)
ax.add_patch(mpatches.FancyBboxPatch((1.5, 1.95), 3.0, 1.15, boxstyle="round,pad=0.06",
                                     fc="#dbeafe", ec=BLEU, lw=1.6))
ax.text(3.0, 2.52, "la barre  [00:00, 01:00)", ha="center", fontsize=11, weight="bold",
        color=ENCRE)
ax.plot([4.42, 4.42], [1.95, 3.10], color=ROUGE, lw=2.0)  # borne EXCLUE
ax.text(4.70, 3.50, "01:00 exclu de la barre\n(un trade à 01:00:00 pile\nappartient à la suivante)",
        fontsize=8.6, color=ROUGE, va="top")

# time / end_time
ax.annotate("bar.time = 00:00\nl'OUVERTURE\n(la clé du CSV)", xy=(1.5, 1.95),
            xytext=(0.45, 3.55), fontsize=10, color=BLEU, weight="bold",
            arrowprops=dict(arrowstyle="->", lw=1.7, color=BLEU,
                            connectionstyle="arc3,rad=-0.2"))
ax.annotate("bar.end_time = 01:00\nla CLÔTURE = l'instant où\non_data reçoit la barre",
            xy=(4.55, 1.72), xytext=(6.9, 3.55), fontsize=10, color=AMBRE, weight="bold",
            arrowprops=dict(arrowstyle="->", lw=1.7, color=AMBRE,
                            connectionstyle="arc3,rad=-0.2", relpos=(0.0, 0.0)))
ax.plot(4.5, 1.6, "o", ms=11, mfc=AMBRE, mec="white", mew=1.5, zorder=6)

# Le piège du fuseau par défaut
ax.text(5.5, 0.42,
        "⚠ PIÈGE : sans set_time_zone(UTC) + add_data(…, TimeZones.UTC), LEAN interprète ces "
        "horodatages naïfs\ncomme de l'heure de NEW YORK — tout glisse de 4-5 h et le trou de "
        "l'heure d'été avale une barre en silence.",
        fontsize=9.6, style="italic", ha="center", color=ROUGE)
fig.tight_layout()
fig.savefig(OUT, dpi=160, bbox_inches="tight")
print("écrit :", OUT)
