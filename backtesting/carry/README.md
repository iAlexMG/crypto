# Carry funding/basis — l'edge structurel qui survit aux frais

Une étude à part sur le banc backtesting : pas une stratégie directionnelle de plus,
mais un **carry delta-neutre** sur le funding des perpétuels BTCUSDT. C'est le
**contrepoint** aux 8 stratégies LEAN — là, les frais tuaient l'edge directionnel
mince ; ici, un edge **structurel** survit aux frais *parce qu'on tient la position*.

> **But** : mesurer honnêtement, sur 7 ans, ce qu'un carry funding rapporte net de
> frais. Public : recruteur quant. La valeur est la rigueur — dire le résultat, y
> compris ses limites, plutôt que de vendre un Sharpe irréaliste.

## L'idée

Sur un perpétuel, il n'y a pas d'échéance : le **funding** (versé toutes les 8h)
rattache le prix du perp au spot. Quand il est positif — 85 % du temps sur BTC, car
les longs paient pour le levier — une position **neutre** *long spot + short perp*
(mêmes quantités BTC → delta ~0 par construction) encaisse ce funding sans pari
directionnel. Le rendement d'un pas de 8h, si déployé :

```
r = funding(T+8h)  −  (basis(T+8h) − basis(T)) / spot(T)        basis = perp − spot
```

Le mouvement de prix s'annule entre les deux jambes ; il reste le carry (funding)
moins la convergence du basis. Décision **causale** : le déploiement au pas k n'utilise
que le funding déjà réalisé (`f_k`), jamais le futur.

## La chaîne

```
fetch_data.py   funding history + klines 8h spot & perp (Binance, gratuit, stdlib)  -> data/*.csv
carry.py        backtest delta-neutre : R0 statique / R2 hystérésis / R1 naïf + témoins
                -> figures carry-equite.png, carry-regime.png (site-content/assets)
```

Données publiques et minuscules (~3 valeurs de funding/jour). `data/` n'est pas versionné
(régénéré par `fetch_data.py`). Env conda `backtesting`.

> ⚠️ **Piège d'alignement (trouvé et corrigé)** : les timestamps de funding de Binance jittent
> de quelques ms et **dérivent avec le temps** ; les klines, elles, sont pile sur la grille 8h.
> On planchonne donc le funding sur la grille 8h avant la jointure. Sinon, un merge sur l'entier
> exact jette ~43 % des périodes — **de façon biaisée** (les années à gros funding dérivent le
> plus), ce qui gonfle le carry mesuré.

```bash
python fetch_data.py           # BTCUSDT depuis 2019-09
python carry.py                # backtest + figures
```

## Le résultat, honnêtement

**Rendement net** (frais taker, rf 4 %/an sur le cash oisif, basis winsorisé, marge unifiée) :

| Fenêtre | Rendement/an | |
|---|---|---|
| 7 ans complets | **+12,3 %** | positif *chaque année* (bear 2022 : +4,2 %) |
| Hors-échantillon 2023-26 | **+7,4 %** | params fixés sur 2019-23 |
| Fenêtre order-flow 2025-26 | **+3,2 %** | régime de funding comprimé |

- **On récolte en TENANT, pas en tradant.** R0 statique ne fait que 2 transactions sur
  7 ans → frais négligeables. Le naïf « short seulement si funding > 0 » (R1) toggle
  plus de mille fois (le funding change de signe au grain 8h) et **les frais cumulés
  dépassent le capital de départ (151 %)** → −8,3 %/an. L'hystérésis (R2, sortir après plusieurs funding négatifs consécutifs)
  ne fait pas mieux : ses meilleurs paramètres in-sample **dégénèrent en statique**
  (déployé 95 %). Timer le funding n'aide pas ; le geste gagnant, c'est de tenir.
- **vs buy & hold** : le carry ne bat pas l'achat-conservation en rendement (+12 vs
  +30 %/an) — il le bat en **risque** (drawdown −9 % contre −77 %). Le bon cadrage est
  « rendement neutre au marché, faible volatilité », pas « battre le marché ».

## Les limites (à lire avant le Sharpe)

- **Le Sharpe n'est pas réel.** La couverture parfaite supposée + l'échantillonnage 8h
  écrasent la volatilité (1,4 %/an → Sharpe ~8). Le **vrai risque** — gaps de basis
  *intra-8h*, appels de marge / liquidation pendant ces gaps, tracking error du hedge —
  n'est pas dans ces données. On lit donc le **rendement et la neutralité**, jamais le
  Sharpe.
- **Régime-dépendant** : 2021 (bull) +36 %/an, 2026 (actuel) +2 %/an. L'edge est réel
  mais son ampleur varie fortement.
- **Déploiement réel** : exige la jambe **spot** (le pilier Automatisation exécute sur
  le perp Bitget) et une marge unifiée entre les deux jambes ; les frais maker/taker
  ne changent rien pour une position tenue (2 trades).
- **Basis winsorisé** à ±100 bps : 2 prints aberrants du perp jeune (2019-20), écartés
  comme artefacts (réversions à un seul pas), pas comme risque supprimé.

## Portée

Comme le reste du banc : la plomberie honnête est le livrable. Extensions naturelles —
alts à funding plus élevé, quantifier le vrai risque de queue avec des données plus
fines, jambe spot exécutable — laissées ouvertes.
