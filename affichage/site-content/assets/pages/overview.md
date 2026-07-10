---
type: overview
titre: "Crypto Orderflow — agrégateur multi-exchanges"
eyebrow: "Data de marché temps réel"
accent: "#00d3a7"
stats:
  - { val: "6", label: "exchanges intégrés" }
  - { val: "11", label: "flux captés en continu" }
  - { val: "~90 j", label: "d'historique (Bitget)" }
  - { val: "Python", label: "PySide6 · pyqtgraph · SQLite WAL" }
stack:
  - "Python 3.12 (conda)"
  - "PySide6 (Qt) + pyqtgraph"
  - "WebSocket + REST par exchange"
  - "SQLite WAL (archive trades + carnet + rollup)"
  - "Threads de fond (collecteur, rollup, recorder) hors thread Qt"
visuel: img/hybride_hero.png
exchanges_order: [bitget, binance, bybit, okx, kucoin, coinbase, kraken]
---

## Pitch

Un terminal orderflow façon Bookmap qui réconcilie 6 exchanges dans une vue
cohérente. Footprint type Quantower en avant-plan, heatmap de liquidité + scatter
de trades + DOM derrière — un seul graphe, un seul axe prix/temps. Tout est
normalisé vers le format Bitget, puis fusionné en une vue hybride avec détection
d'arbitrage en direct.

## L'angle

Le fil rouge n'est pas « j'ai branché 6 exchanges » mais : **chaque exchange s'est
battu pour rentrer dans le moule**. La valeur réelle, ce sont les embûches par
exchange et comment elles ont été résolues — c'est ce qui structure chaque page.

## Parcours

Choisir un exchange → sa page (rôle, init du carnet, résultats, embûches, visuel).
Puis tout converge dans la **vue hybride**, et enfin la **détection d'arbitrage**.
Les briques transversales (rollup, concurrence SQLite, collecteur) sont regroupées
dans **Architecture & ingénierie**.
