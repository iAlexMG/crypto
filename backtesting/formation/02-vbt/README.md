# 02 — vectorbt Pro (vectorisé) — ⏸️ EN ATTENTE

Cette sous-section couvrira le backtesting **vectorisé** avec **vectorbt Pro** : mêmes
stratégies que la sous-section [01 — LEAN](../01-lean/), calculées sur toute la série d'un coup
— idéal pour **explorer** des milliers de variantes en quelques secondes.

## Pourquoi en attente ?

`vectorbtpro` est un paquet **privé sous licence** (dépôt GitHub privé, installation via
`pip install "vectorbtpro @ git+ssh://…"`). La licence n'est **pas encore acquise** : dès
qu'elle le sera, cette sous-section sera construite en miroir de la sous-section LEAN, avec la
même source de vérité OHLCV et les mêmes frais — condition de la **parité** traitée en
[03 — Conclusion & comparaison](../03-conclusion/).

## Plan prévu (miroir de 01 — LEAN)

1. Installation & philosophie vectorisée (vs événementielle).
2. Buy & Hold : `from_holding`, frais, `freq`, `pf.stats()`.
3. Les mêmes stratégies que côté LEAN, une par une.
4. Optimisation de grille (là où vectorbt brille).
