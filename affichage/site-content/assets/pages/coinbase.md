---
id: coinbase
type: exchange
nom: Coinbase
sigle: CB
accent: "#1652f0"
statut: actif-exclu
tagline: "Spot only, side maker inversé — exclu de l'hybride"
marches: ["Spot uniquement"]
stats:
  - { val: "WS 100 %", label: "snapshot puis l2update" }
  - { val: "aucune séquence", label: "best-effort (resync = snapshot à la reco)" }
  - { val: "side = maker", label: "→ à INVERSER pour l'agresseur" }
book_init:
  snapshot: "WebSocket level2_batch type=snapshot (50 niveaux)"
  updates: "WebSocket l2update (changes [side, price, size] ; 0 = suppression)"
  integrite: "Aucune séquence/checksum → best-effort ; watchdog carnet figé 45 s = reco"
schema_svg: svg/coinbase-carnet.svg
visuel: img/coinbase_view.png
---

## Rôle

5e exchange (Spot seul). Intégré et capté en continu, mais **exclu de la vue hybride** :
pas de futures et paires USDT peu liquides.

## Résultats

- Symboles `BTC-USDT` ↔ BTCUSDT re-normalisés ; la GUI bascule auto le menu Marché sur Spot.
- Historique trades paginable (`after=tradeId`) → support complet du collecteur.
- Détection de carnet figé (resync level2 bloquée pendant que les trades passent).

## Embûches & solutions

### side = côté du MAKER (piège classique)
- **Symptôme** — Un match `side="sell"` signifie en réalité qu'un **acheteur** a agressé.
- **Cause** — Coinbase renvoie le côté du maker, pas de l'agresseur.
- **Solution** — Inversion systématique (sell→buy, buy→sell), en WS ET en REST.

### Liquidité USDT faible → exclu de l'hybride
- **Symptôme** — Footprint/heatmap très clairsemés ; beaucoup de « trous » de 30 s sont de vrais creux.
- **Cause** — Les paires USDT de Coinbase sont peu actives (vs leurs paires USD).
- **Solution** — Règle « ne pas s'acharner » (trou vide confirmé → plus retenté) + exclusion de la vue hybride.
