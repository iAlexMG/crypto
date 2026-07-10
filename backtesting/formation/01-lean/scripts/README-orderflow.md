# `orderflow/` — Analyse order-flow multi-échelles (VP, puis Footprint)

Outil **d'observation** (hors formation, pas de backtest) : voir la structure et la
**dynamique** de l'order-flow d'une session à plusieurs résolutions. On applique l'idée
« plusieurs valeurs de paramètre » aux **profils de volume** — échelle temporelle (VP_1m,
VP_5m…) et largeur de value area (VAL/VAH) — et on relie les **sous-VP_Xm** au **VP_fullNY**.

## `vp_multiscale.py` — sous-VP_Xm vs VP_fullNY

Pour une session (NY par défaut) d'une date, sur un canvas **prix × temps**, une figure montre
trois choses et leur dynamique :

1. **les sous-VP_Xm** — la session découpée en briques de X minutes, chaque brique = son mini
   volume-profile (volume par niveau de 25 $), tracé dans son créneau ;
2. **le VP_fullNY** — le profil cumulé de TOUTE la session, en panneau marginal à droite (ce
   vers quoi les sous-VP s'agrègent) ;
3. **la migration de la valeur** — le **POC glissant** (les N dernières minutes, ligne
   violette) suit la valeur EN FORMATION, près du prix — à comparer au **POC de session**
   (ligne noire, fixe) du VP_fullNY.

```powershell
python backtests/orderflow/vp_multiscale.py --date 2026-03-10 --tf 5m
python backtests/orderflow/vp_multiscale.py --date 2026-03-10 --tf 1m --coverage 0.80
python backtests/orderflow/vp_multiscale.py --date 2026-03-10 --session london --tf 15m
```

| option | rôle | défaut |
|---|---|---|
| `--date` | jour NY (données 2026-01 → 2026-06) | 2026-03-10 |
| `--session` | `asia` \| `london` \| `ny` | ny |
| `--tf` | taille des sous-VP : `1m`,`5m`,`15m`,`30m`… | 5m |
| `--coverage` | value area (VAL/VAH), ex. 0.70 = 70 % du volume | 0.70 |
| `--window` | fenêtre du POC glissant, ex. `60m` | 60m |
| `--tick` | taille d'un niveau de prix ($) | 25 |

Sortie : `figures/vp_<date>_<session>_<tf>.png` (gitignorée, reproductible).

## Conventions de rendu (mémoire projet)

Identiques à `formation/01-lean/scripts/fig_vp_prix_temps.py` — la **largeur** d'une brique =
le temps (chaque profil normalisé sur lui-même, jamais par le volume) ; la **couleur** = ratio
delta/volume du niveau ∈ [−1,+1] (rouge = agression vendeuse, vert = acheteuse) ; le **volume
total** revient en panneau du bas ; axe X en **heure de New York** ; sessions et `value_area`
importées de leurs **sources uniques** (`backtests/sessions.py`, `volume_profile_features.py`).

## Prérequis

- Base de ticks `F:/data/BTCUSDT-um.db` (`trades(ts, price, size, side)`), streamée par session.
- Env conda `backtesting` (numpy + matplotlib).

## ⚠️ Analyse, pas signal

Les descripteurs order-flow testés sur ces VP sont sortis **à plat** (aucun edge — cf. coda
mesurée de la leçon 09). Cet outil sert à **voir** la structure de l'enchère (où la valeur se
forme, migre, s'accepte/se rejette), **pas** à générer un signal de trading.

## `footprint_multiscale.py` — footprint (delta par niveau)

Même idée, mêmes briques de ticks, mais lecture = le **delta acheteur/vendeur par niveau** :

1. **sous-footprint_Xm** — chaque brique = un footprint : PAR NIVEAU, barres divergentes
   **vendeur ← (rouge) | → acheteur (vert)**. L'asymétrie = le delta du niveau ;
2. **footprint_fullNY** — en marge, le profil de **delta CUMULÉ par niveau** de la session
   (vert = net acheteur, rouge = net vendeur) : *où* l'agression nette s'est concentrée ;
3. **dynamique** — le niveau de **|Δ| dominant glissant** (N dernières min, violet) suit où
   l'agression nette récente se concentre ; le panneau du bas cumule le **delta de session**
   (le compteur d'agression), et le **delta-POC** (noir) reste la référence de session.

```powershell
python backtests/orderflow/footprint_multiscale.py --date 2026-03-10 --tf 5m
python backtests/orderflow/footprint_multiscale.py --date 2026-03-10 --session london --tf 1m
```

Mêmes options que `vp_multiscale.py` (sauf `--coverage`, propre à la value area du VP).
Sortie : `figures/footprint_<date>_<session>_<tf>.png`. `vp_multiscale.py` et
`footprint_multiscale.py` partagent le streaming des briques (mêmes sources uniques).
