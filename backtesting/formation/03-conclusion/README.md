# 03 — Conclusion & comparaison — ⏳ À VENIR

Cette sous-section finale **réconcilie** les deux moteurs :

1. **Test « sol de vérité »** : sur un Buy & Hold, les deux moteurs doivent retrouver
   `close_dernier / open_premier − 1` (moins les frais). Sinon, la normalisation ou les frais
   sont faux — on corrige avant d'aller plus loin.
2. **Parité stratégie par stratégie** : mêmes signaux, mêmes fills, mêmes P&L ? Chaque écart
   est **expliqué** (sémantique d'exécution, sizing, annualisation des métriques…).
3. **Tableau comparatif final** : quand utiliser quel moteur, limites assumées de chacun, et
   comment les combiner dans un flux de travail pro.

> Prérequis : sous-sections [01 — LEAN](../01-lean/) et [02 — vectorbt](../02-vbt/) terminées.
