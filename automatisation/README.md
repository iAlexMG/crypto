# Crypto — Automatisation (à venir)

Bot Python d'exécution des stratégies sur **Bitget**.

- Développement et validation sur la **démo Bitget** (fonds virtuels, en-tête `paptrading: 1`,
  endpoints WebSocket dédiés) — même plateforme que la production, sans risque.
- Réutilise le connecteur Bitget déjà présent dans l'affichage
  (`../affichage/backend/connectors/bitget.py`, `bitget_rest.py`).
- Passage en réel une fois les stratégies validées en démo.
