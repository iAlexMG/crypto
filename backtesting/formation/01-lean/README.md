# 01 — QuantConnect LEAN (événementiel) — ✅ COMPLÈTE (9/9 leçons)

Backtesting **événementiel** avec le moteur open-source **QuantConnect LEAN**, compilé
**depuis les sources GitHub** et exécuté **nativement, sans Docker**.

## Pourquoi « depuis les sources, sans Docker » ?

La voie officielle grand public est la CLI `lean`, qui exécute le moteur dans un conteneur
Docker. C'est pratique, mais :

- **opaque** : le moteur est une boîte noire qu'on ne peut ni lire ni déboguer ;
- **lourd** : Docker Desktop doit tourner, images de plusieurs Go ;
- **pédagogiquement pauvre** : on apprend une CLI, pas un moteur.

Compiler LEAN soi-même (`dotnet build`) donne un moteur **transparent** (on lit le code qui
remplit nos ordres), **débogable** (points d'arrêt dans l'algorithme ET dans le moteur) et
**léger** (un simple processus .NET). C'est la voie « ingénieur » — celle de cette formation.

## Leçons publiées (9/9)

| # | Leçon | Au programme |
|---|-------|--------------|
| 01 | [Compiler et lancer LEAN sans Docker](lecons/lecon-01-installation.md) | Cloner, compiler (.NET), premier backtest natif, brancher Python 3.11 |
| 02 | [Le moteur à l'œuvre et nos données BTCUSDT](lecons/lecon-02-moteur-et-donnees.md) | Boucle événementielle, sémantique des fills mesurée (séance + hors séance), lecteur `PythonData` custom, contrat UTC vérifié, falsification du fuseau (barre avalée le 8 mars) |
| 03 | [Buy & Hold : le sol de vérité](lecons/lecon-03-buy-and-hold.md) | `SymbolProperties` (lot 1 satoshi), frais 0,04 %, équité recalculée à la main au centime près, falsification sans propriétés |
| 04 | [Suivre la tendance : SMA et MACD](lecons/lecon-04-tendance-sma-macd.md) | Croisement SMA 50/200 vs MACD 12/26/9 : un seul facteur isolé — la réactivité (22 vs 341 trades), le lent écrase le rapide |
| 05 | [Le retour à la moyenne : RSI et Bollinger](lecons/lecon-05-retour-moyenne-rsi-bollinger.md) | Acheter la faiblesse : RSI 14 (trade catastrophe −27 %) et Bollinger 20/2σ (sur-trading, tyrannie des frais) |
| 06 | [La gestion du risque](lecons/lecon-06-gestion-risque.md) | Stop/take greffés sur le RSI, le paradoxe « coupe le désastre mais rogne le total », sizing |
| 07 | [Métriques et optimisation](lecons/lecon-07-metriques-optimisation.md) | Sharpe/Sortino/Calmar, le piège du win rate, l'expectancy ; grille + heatmap, in-sample/out-of-sample, le meilleur IS s'effondre OOS |
| 08 | [La stratégie avancée : tout assembler](lecons/lecon-08-strategie-avancee.md) | Régime + croisement MACD confirmé RSI + sizing au risque (ATR) + stops ; tableau de bord final, limites assumées, pont vers vbt |
| 09 | [Le volume profile par session](lecons/lecon-09-volume-profile.md) | Volume par prix reconstruit des ticks, ancré sur les sessions Asia/London/NY (heure NY, DST), sous-VP intra-session, filtre delta mesuré (+11,6 pts), sensibilité à l'ancrage, look-ahead falsifié |

Le plan complet de la section (sujets et objectifs) est aussi dans le
[résumé des leçons](../README.md#résumé-des-leçons) de la formation.

## Arborescence de travail

```
backtests/
  lean/          # clone du dépôt QuantConnect/Lean (moteur + données d'exemple) — NON versionné ici
  algorithms/    # nos algos de cours : buyhold.py, sma_croisement.py, … (hors du clone, chemin relatif depuis le Launcher)
formation/01-lean/
  lecons/        # les 9 leçons de cette sous-section
  assets/images/ # PNG générés par les scripts
  scripts/       # scripts reproductibles qui génèrent les figures (fig_*.py)
```
