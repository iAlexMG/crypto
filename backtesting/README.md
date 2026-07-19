# Crypto — Backtesting (LEAN + vectorbt sur BTCUSDT)

Backtests des 8 stratégies sur BTCUSDT (futures perpétuels margés USDT).
Arborescence **miroir** du frère `indicesBoursiers/backtesting` depuis le
2026-07-19 — mêmes recettes, mêmes emplacements des deux bords.

## La chaîne complète (de la donnée au backtest)

```
../historique/                           extraction Binance (aggTrades) -> base SQLite de ticks
backtests/volume_profile_features.py     ticks -> features_vp.csv (POC/VAH/VAL par session)
backtests/algorithms/*.py                les 8 stratégies LEAN + anatomies/lecteurs des leçons
backtests/lean/                          moteur QuantConnect LEAN compilé des sources — NON versionné
```

## Modules

- `backtests/algorithms/` — les 8 stratégies, plus les algos d'anatomie et de
  lecture de données écrits pour les leçons.
- `backtests/sessions.py` — découpage Asia/London/NY en heure de New York.
  ⚠️ **Miroir** de `indicesBoursiers/backtesting/backtests/sessions.py` (même
  chemin des deux bords) : reporter toute correction dans les deux.
- `backtests/volume_profile_features.py` — features POC/VAH/VAL par session
  depuis les ticks ; `--profil-jour` écrit le PNG concept de la leçon 09
  directement dans la formation.

## Environnement

conda `backtesting` (Python 3.11) — voir `requirements.txt`. Source unique :
le frère indices s'y réfère aussi.

## La formation

La formation LEAN/vectorbt qui s'appuie sur ces backtests vit dans
`Portfolio/Formations/Trading` (déménagée le 2026-07-19, avec les cours Python
et GitHub). Ses scripts de figures se lancent toujours depuis ce dossier-ci :

```bash
python ../../Formations/Trading/01-lean/scripts/fig_metriques.py
```
