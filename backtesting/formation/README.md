# Backtesting de pro avec LEAN et vectorbt — Formation

Construire, **de zéro**, un backtesting de niveau professionnel sur **deux moteurs
complémentaires**, appliqué à de **vraies données crypto** (futures perpétuels BTCUSDT,
marge USDT). Deux formations autonomes, une par moteur — les **résultats mesurés** des
stratégies vivent, eux, dans le pilier **Backtesting** du hub crypto.

## Public visé & prérequis

Formation **niveau intermédiaire → avancé**. On suppose que tu es **déjà à l'aise** :

- **Python** : fonctions, classes, pandas/NumPy de base.
- **Marchés & trading** : ordres, position longue/short, notions d'indicateurs.
- **Backtesting** : tu sais ce que c'est ; on approfondit la **rigueur** (biais, frais,
  exécution réaliste, parité entre moteurs).

Pas besoin de connaître LEAN ni vectorbt au départ : on les apprend ici.

## Structure de la formation — deux sous-sections

| Sous-section | Moteur | Statut |
|---|---|---|
| [**01 — LEAN**](01-lean/) | QuantConnect LEAN (événementiel), compilé **depuis les sources GitHub, sans Docker** | ✅ Complète (9/9) |
| [**02 — vectorbt**](02-vbt/) | vectorbt Pro (vectorisé) | ⏸️ En attente (licence) |

> Deux moteurs, deux forces complémentaires : **LEAN** rejoue le marché barre par barre —
> réaliste, débogable, orienté production ; **vectorbt** calcule sur toute la série d'un coup —
> idéal pour explorer des milliers de variantes en quelques secondes. Deux lectures
> complémentaires du même backtest — chacune une formation autonome.

## Résumé des leçons

> Plans **adaptatifs** : les intitulés au-delà des leçons déjà publiées peuvent évoluer avec la
> construction du cours. ✅ = publiée.

### Sous-section 01 — LEAN

| # | Leçon | Sujet & objectif |
|---|-------|------------------|
| 01 ✅ | [Compiler et lancer LEAN sans Docker](01-lean/lecons/lecon-01-installation.md) | Cloner et compiler le moteur (.NET), lancer un premier backtest natif C# puis Python (`PYTHONNET_PYDLL`) ; **savoir exécuter LEAN chez soi, capot ouvert**. |
| 02 ✅ | [Le moteur à l'œuvre et nos données BTCUSDT](01-lean/lecons/lecon-02-moteur-et-donnees.md) | Boucle événementielle, sémantique des fills **mesurée** (séance + hors séance), lecteur custom `PythonData`, contrat OHLCV UTC vérifié, falsification du fuseau (barre avalée le 8 mars) ; **savoir ce que le moteur fait de nos ordres et lui faire consommer notre CSV**. |
| 03 ✅ | [Buy & Hold : le sol de vérité](01-lean/lecons/lecon-03-buy-and-hold.md) | Premier backtest sur nos données : `SymbolProperties` (lot 1 satoshi), frais taker 0,04 %, équité recalculée à la main au centime près, falsification sans propriétés ; **valider toute la chaîne données→résultat**. |
| 04 ✅ | [Suivre la tendance : SMA et MACD](01-lean/lecons/lecon-04-tendance-sma-macd.md) | Croisement SMA 50/200 vs MACD 12/26/9 — même camp, un seul facteur isolé : la réactivité (22 vs 341 trades) ; **passer du signal à l'exécution et accorder les paramètres au timeframe**. |
| 05 ✅ | [Le retour à la moyenne : RSI et Bollinger](01-lean/lecons/lecon-05-retour-moyenne-rsi-bollinger.md) | Acheter la faiblesse : RSI 14 (seuils fixes, trade catastrophe −27 %) et Bollinger 20/2σ (seuils adaptatifs, sur-trading et tyrannie des frais) ; **contraster tendance et contre-tendance sur le même marché**. |
| 06 ✅ | [La gestion du risque](01-lean/lecons/lecon-06-gestion-risque.md) | Stop-loss/take-profit greffés sur le RSI (signal inchangé), le paradoxe « le stop coupe le désastre mais rogne le total », sizing ; **séparer la logique de signal de la logique de survie**. |
| 07 ✅ | [Métriques et optimisation](01-lean/lecons/lecon-07-metriques-optimisation.md) | Sharpe/Sortino/Calmar, le piège du win rate, l'expectancy comme seul juge ; grille SMA + heatmap, in-sample/out-of-sample : le meilleur IS s'effondre là où le réglage fixe tient ; **lire un rapport comme un pro et reconnaître un backtest trop beau pour être vrai**. |
| 08 ✅ | [La stratégie avancée : tout assembler](01-lean/lecons/lecon-08-strategie-avancee.md) | Régime SMA 200 + croisement MACD confirmé RSI, sizing au risque (1 %, ATR), stop suiveur/take/sortie de régime ; tableau de bord final des 7 backtests, limites assumées ; **consolider avant le second moteur**. |
| 09 ✅ | [Le volume profile par session](01-lean/lecons/lecon-09-volume-profile.md) | Reconstruire depuis les ticks le volume par prix (POC, value area 70 %) **ancré sur les sessions** Asia/London/NY (heure de New York, DST) avec sous-VP intra-session, delta acheteur/vendeur comme filtre mesuré (+11,6 pts), sensibilité à l'ancrage (un « edge » qui s'évapore), falsification du look-ahead ; **brancher des features order-flow sans casser la causalité**. |

### Sous-section 02 — vectorbt *(prévisionnel — en attente de licence)*

| # | Leçon | Sujet & objectif |
|---|-------|------------------|
| 01 | Installer vectorbt Pro & la philosophie vectorisée | Signaux booléens sur toute la série d'un coup vs rejeu barre par barre ; **changer de paradigme sans changer de stratégie**. |
| 02 | Buy & Hold vectorisé | `from_holding`, frais, `freq`, `pf.stats()` ; **reproduire la leçon 03 de LEAN en quelques lignes**. |
| 03 | Les mêmes stratégies, vectorisées | SMA, RSI, Bollinger, MACD via `from_signals` ; **écrire une fois la logique, l'exécuter en millisecondes**. |
| 04 | L'optimisation massive | Grilles complètes, heatmaps, milliers de combinaisons ; **exploiter la vraie force du vectorisé**. |
| 05 | Walk-forward | Fenêtres glissantes entraînement→test ; **valider qu'un paramètre survit hors échantillon**. |
| 06 | Synthèse vectorbt | Bilan de la section ; **consolider les résultats vectorisés**. |

## Les données

`BTCUSDT` **futures perpétuels margés en USDT** (Binance — catégorie officielle « USDⓈ-M »,
c'est-à-dire margée en stablecoin), trades tick-par-tick (aggTrades) stockés en
SQLite — un historique qui **s'étend au fil du cours**. Les leçons calculent leurs chiffres
depuis les données réellement chargées : **aucun chiffre figé**.

## Format & conventions

- Leçons en **Markdown** dans `<sous-section>/lecons/`.
- Visuels : **PNG** (schémas, graphiques) et **MP4** (animations) dans
  `<sous-section>/assets/`, générés par des **scripts reproductibles** conservés dans
  `<sous-section>/scripts/`. Chaque visuel a une **légende** et un **texte alternatif**.

## Avertissement

Contenu **pédagogique**. Rien ici n'est un conseil financier. Les performances passées (et *a
fortiori* simulées) ne préjugent pas des performances futures.
