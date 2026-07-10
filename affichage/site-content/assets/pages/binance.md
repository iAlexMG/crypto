---
id: binance
type: exchange
nom: Binance
sigle: BN
accent: "#f0b90b"
statut: actif
tagline: "Le plus liquide — l'ancre du recalage hybride"
marches: [Spot, "USDⓂ Futures"]
stats:
  - { val: "REST + WS", label: "snapshot REST puis diffs WS" }
  - { val: "U / u / pu", label: "intégrité par séquence (pas de checksum)" }
  - { val: "ancre", label: "du recalage hybride (le plus liquide)" }
book_init:
  snapshot: "REST /depth (limit=1000) → lastUpdateId"
  updates: "WebSocket <sym>@depth@100ms, raccordées par numéro de séquence"
  integrite: "Continuité de séquence (SPOT U/u ; FUTURES pu==last_u) → resync sur trou"
schema_svg: svg/binance-carnet.svg
visuel: img/binance_view.png
---

## Rôle

Le flux le plus tradé du périmètre. Sert d'**ancre** pour estimer le basis du recalage
hybride (Bitget est trop peu tradé pour un appariement fiable : ~17 trades/30 s contre
~222 pour Binance).

## Résultats

- Synchronisation snapshot REST + buffer de diffs WS, robuste aux snapshots décalés (attente/refetch sans boucle).
- Historique trades profond via aggTrades, pagination par `fromId` (sans contrainte de fenêtre 1 h).
- Seek direct par temps (`fetch_range` / startTime) → comblage d'un trou sans repaginer depuis le présent.

## Embûches & solutions

### @aggTrade mort sur les futures
- **Symptôme** — Sur `fstream`, le flux `@aggTrade` ne pousse aucun event (0 trade) alors que `@depth` fonctionne.
- **Cause** — Comportement non documenté de l'endpoint futures.
- **Solution** — FUTURES → `@trade` (trades individuels, id `t`) ; SPOT garde `@aggTrade` (id `a`).

### Trades parasites à prix 0
- **Symptôme** — Le canal `@trade` futures émet des trades `X="NA"` à prix 0 → mèches infinies + faux trous dans le footprint.
- **Cause** — Fonds d'assurance / ADL injectés dans le flux trade.
- **Solution** — Filtre strict `X=="MARKET"` et `prix>0` dans `_on_trade`.

### Carnet figé non détecté par le watchdog
- **Symptôme** — Le carnet gelait (resync bloquée) alors que les trades continuaient → watchdog aveugle.
- **Cause** — Le watchdog ne surveillait que l'âge du dernier message, pas l'âge du carnet.
- **Solution** — Ajout d'un suivi `_last_book` (gel >45 s = reco) + ancrage du bord droit Live sur le dernier trade.

### Dédup live/REST impossible par id
- **Symptôme** — Le volume futures doublait quand le REST recouvrait une plage déjà couverte par le live.
- **Cause** — Ids `@trade` (`t`) ≠ ids REST aggTrades (`a`) → la dédup par trade_id ne les croise pas.
- **Solution** — Partition par intervalles de couverture live (`_live_iv`) : le REST comble les creux de coupure sans recouvrir les plages live.
