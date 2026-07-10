---
id: hybride
type: feature
titre: "Vue hybride — l'aboutissement"
eyebrow: "Aboutissement"
accent: "#00d3a7"
schema_svg: svg/recalage-hybride.svg
visuel: img/hybride_view.png
protections:
  - titre: "1 — Basis figé par bucket de 60 s"
    desc: "Le basis n'est pas un scalaire global : c'est une carte {bucket60 → offset}. Chaque bucket est calculé une seule fois puis gelé. Évite que tout le footprint d'un exchange saute de position quand la médiane frémit."
  - titre: "2 — Découplage ancre / référence"
    desc: "L'axe affiché reste Bitget, mais le basis est estimé contre le flux le plus liquide (Binance), car Bitget est trop peu tradé. offset(F) = basis(F↔ancre) − basis(Bitget↔ancre). Mesuré : appariement 89–98 % vs Binance contre 11–53 % vs Bitget."
  - titre: "3 — Gel par trade"
    desc: "Le prix recalé de chaque trade est calculé une seule fois (à sa 1ère vue) puis caché par trade_id. Un trade déjà affiché ne rebouge jamais, même quand le basis du bucket courant continue de se former."
basis_examples:
  - { ex: "Bybit",  val: "≈ +5,9 $ (~1 bps)",     note: "stable" }
  - { ex: "OKX",    val: "≈ −2,9 $ (~−0,5 bps)",  note: "stable" }
  - { ex: "KuCoin", val: "≈ −21 $ (~−3,5 bps)",   note: "venue moins liquide, vrai écart" }
phases:
  - { titre: "Phase 1 — Footprint hybride", statut: "Fait" }
  - { titre: "Phase 2 — Heatmap + DOM hybride (live)", statut: "Fait (live)" }
  - { titre: "Phase 3 — Arbitrage", statut: "Fait" }
---

## Intro

Les exchanges ne tradent pas au même prix absolu (quelques $ d'écart : basis perp/spot,
liquidité, frais). La vue hybride décale chaque exchange sur l'axe de prix Bitget, puis
somme leur orderflow en un seul footprint, un seul DOM cumulé et une heatmap fusionnée.
Périmètre : **Bitget + Binance + Bybit + OKX + KuCoin** (Coinbase exclu).

## Le cœur : recaler les prix sur Bitget

> **L'erreur évitée.** Le recalage NE se fait PAS sur le spread des mids de carnet :
> ceux-ci sont biaisés par le lag asynchrone entre carnets (un carnet bouge avant l'autre
> pendant un mouvement). Le basis est estimé sur les prix **tradés**, par appariement
> temporel des trades (≤500 ms), médiane sur 60 s. L'appariement annule le timing → vrai
> offset.

(Schéma avant/après : `svg/recalage-hybride.svg`.)

## Trois protections cumulées

Voir le bloc `protections` du frontmatter — chaque protection est un titre + description
prête à rendre en carte.

## Basis observé par venue (mesuré en live)

Voir le bloc `basis_examples` (Venue / Basis Futures / Note).

## Détail des phases

### Phase 1 — Footprint hybride · *Fait*
Pour chaque flux : lire son rollup, décaler ses rangs de prix du basis du bucket, puis
sommer les volumes bid/ask dans la grille unifiée. Bitget non décalé (référence). Bitget
décochable : il reste la référence d'axe même décoché, on masque juste son volume.

### Phase 2 — Heatmap + DOM hybride (live) · *Fait (live)*
DOM = un seul carnet cumulé (somme des flux recalés, anti-croisement au mid Bitget).
Heatmap = somme recalée des carnets en mémoire. Limite : couvre le live ~2 h ; l'historique
profond disque (multi-flux) reste à faire.

### Phase 3 — Arbitrage · *Fait*
Voir la page Arbitrage.
