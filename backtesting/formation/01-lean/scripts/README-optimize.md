# `optimize/` — Balayage de paramètres avec le vrai moteur LEAN + heatmaps 2D/3D

Outil **réutilisable** (hors formation) qui fait ce que vectorbt fait en un `.run()` : tester
une **grille de combinaisons de paramètres** pour une stratégie, en garder les métriques, et
sortir des **heatmaps** — 2D annotée, 3D statique, et **3D interactive** qu'on fait tourner à
la souris.

La différence avec un balayage vectorisé (celui de la leçon 07) : ici **chaque point de la
grille est un backtest LEAN complet et exact**, pas une approximation. On réplique la mécanique
de l'optimiseur natif de LEAN (`Optimizer.Launcher/ConsoleLeanOptimizer.cs`), qui lance lui
aussi un processus LEAN par combinaison avec `--parameters`. Plus lent qu'un proxy vectorisé
(~9 s/combo), mais c'est la **source de vérité** — et la parallélisation absorbe le coût.

## Prérequis (déjà en place dans ce projet)

- Moteur LEAN compilé : `backtests/lean/Launcher/bin/Release/QuantConnect.Lean.Launcher.dll`
  (cf. [leçon 01](../../formation/01-lean/lecons/lecon-01-installation.md)).
- Env conda `backtesting` (Python 3.11 + pandas). Détecté automatiquement ; sinon poser
  `LEAN_PY_ENV` sur le dossier qui contient `python311.dll`.
- `plotly` (pour la 3D interactive) : `pip install plotly`. matplotlib assure une 3D statique
  sans dépendance.
- Le CSV de données : `F:/data/ohlcv/BTCUSDT-um/1H.csv`.

## Stratégies disponibles

Chacune balaie **2 paramètres** (heatmap 2D) ; les défauts reproduisent le réglage de la
formation au chiffre près (contrôle de conformité).

| `strat` | Paramètres balayés | Fixé | Défaut = formation |
|---|---|---|---|
| `sma` | `period_fast` × `period_slow` | — | 50 / 200 |
| `macd` | `ema_fast` × `ema_slow` | signal = 9 | 12 / 26 |
| `bollinger` | `period_bb` × `k_std` (float) | — | 20 / 2.0 σ |
| `rsi` | `rsi_period` × `seuil` | surachat = 100 − seuil | 14 / 30 (→ 30/70) |

## Usage

```powershell
# 1) Balayer la grille d'une stratégie — écrit results/<strat>_grid.csv
python backtests/optimize/run_grid.py sma        --workers 5
python backtests/optimize/run_grid.py macd       --workers 5
python backtests/optimize/run_grid.py bollinger  --workers 5
python backtests/optimize/run_grid.py rsi        --workers 5

#    sous-ensemble / conformité : surcharger un paramètre (--set répétable)
python backtests/optimize/run_grid.py sma --set period_fast=20,50 --set period_slow=100,200
#    --force : ignore le cache et relance tout   --target : métrique de « le meilleur »

# 2) Tracer les heatmaps depuis le CSV
python backtests/optimize/heatmap.py sma --metric sharpe --open
python backtests/optimize/heatmap.py rsi --metric net_profit
#    --metric : sharpe | sortino | net_profit | car | drawdown | psr | ...
#    --open   : ouvre la surface 3D interactive dans le navigateur

# 3) JUGER l'optimisation : IS/OOS et walk-forward (réutilise le cache, AUCUN re-run LEAN)
python backtests/optimize/walkforward.py sma                 # coupe IS/OOS 50/50 + double heatmap
python backtests/optimize/walkforward.py rsi --min-trades 6  # écarte les combos dégénérés (0 trade)
python backtests/optimize/walkforward.py sma --folds 6       # walk-forward ancré
```

## Ce que ça produit

| Fichier (dans `results/`) | Contenu |
|---|---|
| `sma_grid.csv` | une ligne par combinaison : paramètres + toutes les métriques |
| `sma_heatmap_<metric>.png` | heatmap 2D annotée, meilleur combo encadré |
| `sma_surface3d_<metric>.png` | surface 3D statique |
| `sma_surface3d_<metric>.html` | **surface 3D interactive** (rotation/zoom/survol) |
| `<strat>_is_oos.png` | double heatmap IN-SAMPLE vs OUT-OF-SAMPLE (`walkforward.py`) |
| `<strat>_walkforward_<K>.png` | rendement OOS enchaîné : ré-optimisé vs référence fixe |

Les sorties LEAN brutes (un dossier complet par combo) vont dans `runs/<strat>/<combo>/` —
**gitignorées** et **mises en cache** : relancer `run_grid.py` réutilise les combos déjà
calculés (sauf `--force`).

## Comment ça marche

1. **L'algo est paramétrable.** `algos/sma_optimizable.py` est le clone exact de la SMA de la
   formation, à une chose près : les périodes viennent de `self.get_parameter("period_fast", 50)`
   / `period_slow` au lieu d'être figées. C'est le mécanisme natif que lit aussi l'optimiseur.
   Contrôle de conformité : en (50,200) il reproduit `SmaCroisement` au chiffre près
   (Net Profit −4,28 %, 827 $ de frais, Sharpe −0,464).
2. **L'orchestrateur** (`run_grid.py`) lance, pour chaque combo et en parallèle :
   `dotnet …Launcher.dll --parameters period_fast:X,period_slow:Y --results-destination-folder <dir> --close-automatically true`
   depuis `Launcher/bin/Release` (chemins relatifs de `config.json`).
3. **Le succès se juge sur le `-summary.json`, pas sur le code de sortie.** LEAN lève une
   exception `ResultsAnalyzer` post-backtest (exit 82) sur notre donnée custom, mais les
   fichiers de résultats sont déjà écrits — l'orchestrateur lit le JSON et l'ignore.

## Ajouter une stratégie

1. Cloner l'algo en version paramétrable dans `algos/` (lire les réglages via `get_parameter`).
2. Ajouter une entrée au dictionnaire `STRATEGIES` de `run_grid.py` (classe, fichier, grille,
   `valid` de validité, et `default` = combo de référence pour `walkforward.py`). Deux
   paramètres → heatmap 2D/3D + IS/OOS directement.

## ⚠️ « Le meilleur » = in-sample → le juge, c'est `walkforward.py`

`run_grid.py` imprime le meilleur combo **sur les données vues** — c'est de l'optimisation
**in-sample (IS)**, donc sujette au **sur-apprentissage**. Le juge honnête est
l'**out-of-sample (OOS)** : `walkforward.py` réserve une portion jamais vue et compare.

- **`--split 0.5`** : optimise sur la 1ʳᵉ moitié (IS), teste le champion sur la 2ᵈ (OOS), et
  trace la **double heatmap** IS vs OOS. Sur nos données, RSI est le cas d'école : le
  champion IS (21,30) fait −3 % IS puis **s'effondre à −19 % OOS**, battu par la référence
  non-optimisée — *optimiser a nui*. SMA (80,300) au contraire **tient** (plateau robuste).
- **`--folds K`** : walk-forward ancré (ré-optimise sur le passé, teste sur le fold suivant).
- **`--min-trades N`** : à passer à walkforward pour ne pas couronner un combo **dégénéré**.

C'est la démonstration de la [leçon 07](../../formation/01-lean/lecons/lecon-07-metriques-optimisation.md),
faite ici avec le vrai moteur. L'outil **mesure** l'espace des paramètres ; seul l'OOS dit si
un réglage **tient**.

**Piège « combo dégénéré »** : sur un marché baissier, *ne rien faire* (0 % de rendement) bat
toute stratégie perdante. Un balayage peut donc couronner un réglage qui **ne trade jamais**
(ex. RSI seuil 10 → survente 10/surachat 90, quasi jamais franchi). C'est pourquoi la ligne
`[meilleur]` affiche `trades=` : un meilleur à **0 trade** n'est pas une stratégie. Regarde la
colonne `total_orders` du CSV avant de conclure.
