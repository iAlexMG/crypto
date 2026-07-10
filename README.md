# Crypto — de la donnée à l'exécution

Chaîne complète order-flow sur crypto (BTCUSDT), en 4 piliers :

| Pilier | Contenu |
|---|---|
| `historique/` | Extraction & normalisation des données (historique Binance → chandelles) |
| `affichage/` | Agrégateur order-flow multi-exchanges, rendu temps réel (pyqtgraph) |
| `backtesting/` | Backtesting LEAN + vectorbt sur BTCUSDT, + la formation |
| `automatisation/` | Bot Python d'exécution sur Bitget (démo → réel) — *à venir* |
