---
id: architecture
type: feature
titre: "Architecture & ingénierie"
eyebrow: "Transversal"
accent: "#7b61ff"
blocs:
  - titre: "Rollup pré-agrégé du footprint"
    desc: "Reconstruire le footprint depuis les trades bruts coûte O(trades) → ~300 ms/frame en zoom arrière (gel + GIL saturé). Parade : un rollup base 60 s × tick × side, maintenu incrémentalement par un curseur sur le rowid (capte tout insert, même hors ordre, une seule fois). Coût de lecture borné par l'affichage, ~3-5 ms/frame côté Qt."
  - titre: "Concurrence SQLite (WAL)"
    desc: "Écriture = 1 connexion + lock ; lectures = connexion read-only par thread (query_only=1), hors lock. Un agrégat lourd (~4 s) ne bloque plus l'archivage live ni le collecteur (insert 0,2 ms médian mesuré pendant 2 agrégats concurrents)."
  - titre: "Collecteur d'historique — 1 thread par exchange"
    desc: "Parallélisme réseau : un exchange lent/rate-limité n'affame plus les autres. Ancrage Bitget (plancher commun). Phase A = comble tous les trous (>30 s) du présent au plancher ; Phase B = passé profond. Trou vide confirmé → plus retenté ; erreur/429 → cooldown."
  - titre: "Tout le lourd hors thread Qt"
    desc: "Footprint/heatmap historiques, lectures disque, rollup : threads de fond + cache + debounce. Le thread Qt ne fait que rendre → le GIL n'est jamais saturé et le collecteur n'est pas affamé. La navigation profonde ne gèle jamais."
  - titre: "Rétention bornée"
    desc: "Purge horaire des trades bruts >7 j ET déjà agrégés (le footprint survit dans le rollup ; les scatters ne couvrent que la dernière heure) + purge des vieux snapshots de carnet → les bases ne grossissent plus sans fin."
  - titre: "Lignes bid/ask reconstruites"
    desc: "Le carnet historique n'existe que là où l'app a tourné. Ailleurs : un acheteur agresseur a tapé l'ask, un vendeur le bid → on reconstruit la ligne depuis les trades. Fusion carnet réel (prioritaire) + reconstruction dans les trous, coupée sur les vrais trous de données."
---

## Intro

Les briques transversales qui n'appartiennent à aucun exchange en particulier, mais qui
font tenir l'ensemble en temps réel sans gel.

## Briques

Voir le bloc `blocs` du frontmatter — chaque entrée est un titre + description prête à
rendre en carte.
