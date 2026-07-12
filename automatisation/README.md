# Crypto — Automatisation (à venir)

Bot Python : **signaux calculés sur les données Binance, exécution automatisée sur Bitget**.

## Architecture décidée (2026-07-11)

- **Décision et exécution séparées** : les signaux naissent sur le marché de référence
  (Binance — la même donnée que le pilier historique et les backtests) ; les ordres
  partent sur l'API de Bitget.
- **Pont hybride** : le recalage basis trade-based construit pour l'affichage traduit en
  continu les prix Binance en prix exécutables Bitget ; la détection de dislocation sert
  de garde-fou (réduction de taille ou arrêt quand les venues divergent ou qu'un flux
  devient suspect).

## Démarche

- Développement et validation sur la **démo Bitget** (fonds virtuels, en-tête `paptrading: 1`,
  endpoints WebSocket dédiés) — même plateforme que la production, sans risque.
- Réutilise le connecteur Bitget déjà présent dans l'affichage
  (`../affichage/backend/connectors/bitget.py`, `bitget_rest.py`).
- Passage en réel une fois les stratégies validées en démo, avec des garde-fous
  d'exécution : taille de position limitée, arrêt d'urgence, journal des ordres.
