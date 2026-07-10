"""Figure : deux façons d'exécuter LEAN — CLI+Docker vs compilation des sources."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

OUT = "formation/01-lean/assets/images/lecon-01-docker-vs-source.png"

fig, ax = plt.subplots(figsize=(11, 5.2))
ax.set_xlim(0, 11); ax.set_ylim(0, 5.2); ax.axis("off")

def box(x, y, w, h, text, fc, ec="#333", fs=10, weight="normal"):
    ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08",
                                         fc=fc, ec=ec, lw=1.2))
    ax.text(x + w/2, y + h/2, text, ha="center", va="center", fontsize=fs, weight=weight)

def arrow(x1, y1, x2, y2):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="->", lw=1.6, color="#333"))

# --- Voie 1 : CLI + Docker (grise, opaque)
ax.text(2.6, 4.85, "Voie « grand public » : CLI + Docker", fontsize=12, weight="bold", ha="center")
box(0.3, 3.4, 2.0, 0.8, "lean backtest\n(CLI Python)", "#e8e8e8")
box(2.9, 3.4, 2.4, 0.8, "Docker Desktop\n(démon + image ~Go)", "#d9d9d9")
box(0.3, 1.9, 5.0, 0.9, "Conteneur : moteur PRÉCOMPILÉ\n(boîte noire : ni lisible, ni débogable)", "#c9c9c9")
arrow(2.3, 3.8, 2.9, 3.8)
arrow(4.1, 3.4, 4.1, 2.8)
box(0.3, 0.4, 5.0, 0.8, "Résultats du backtest", "#e8e8e8")
arrow(2.8, 1.9, 2.8, 1.2)

# --- Voie 2 : sources GitHub (couleur, transparente)
ax.text(8.35, 4.85, "Notre voie : les sources GitHub", fontsize=12, weight="bold", ha="center")
box(6.1, 3.4, 2.0, 0.8, "git clone\nQuantConnect/Lean", "#dbeafe")
box(8.7, 3.4, 2.0, 0.8, "dotnet build\n(SDK .NET 10)", "#dbeafe")
box(6.1, 1.9, 4.6, 0.9, "Moteur COMPILÉ PAR NOUS : code lisible,\npoints d'arrêt possibles + Python 3.11 (pythonnet)", "#bfdbfe")
arrow(8.1, 3.8, 8.7, 3.8)
arrow(9.7, 3.4, 9.7, 2.8)
box(6.1, 0.4, 4.6, 0.8, "Résultats du backtest (identiques)", "#dbeafe")
arrow(8.4, 1.9, 8.4, 1.2)

ax.text(5.55, 0.05, "Deux chemins, le même moteur — mais un seul se laisse ouvrir, lire et déboguer.",
        fontsize=10, style="italic", ha="center")
fig.tight_layout()
fig.savefig(OUT, dpi=150, bbox_inches="tight")
print("écrit :", OUT)
