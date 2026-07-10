---
id: bybit
type: exchange
nom: Bybit
sigle: BY
accent: "#f7a600"
statut: actif
tagline: "Carnet 100 % WebSocket — mais sans historique profond"
marches: [Spot, "Linear (USDT perp)"]
stats:
  - { val: "WS 100 %", label: "snapshot puis delta (comme Bitget)" }
  - { val: "séquence u", label: "intégrité (u == last_u+1), pas de checksum" }
  - { val: "récent seul", label: "REST sans pagination → forward only" }
book_init:
  snapshot: "WebSocket type=snapshot (orderbook.200) → reset du carnet local"
  updates: "WebSocket type=delta (taille 0 = suppression)"
  integrite: "Numéro de séquence u (trou → raise → reco = snapshot frais)"
schema_svg: svg/bybit-carnet.svg
visuel: img/bybit_view.png
---

## Rôle

Intégré de bout en bout (carnet + trades live). Sa limite d'API en fait un cas
intéressant : l'historique des trades n'existe que vers l'avant.

## Résultats

- Carnet profondeur 200 (@100 ms), 150 meilleurs niveaux poussés au hub (aligné Bitget/Binance).
- Ping applicatif `{"op":"ping"}` + reset systématique sur snapshot spontané.
- `side` (S) = côté du taker = agresseur directement (pas d'inversion).

## Embûches & solutions

### Aucun historique REST profond
- **Symptôme** — `recent-trade` ≤1000 (spot ≤60), sans startTime ni idLessThan → un trou profond ne se comble jamais.
- **Cause** — L'API publique n'expose pas de trades paginables.
- **Solution** — Historique forward only (sessions où l'app tourne) ; trou profond marqué *settled* au lieu de s'acharner. `fetch_range = None`.

### trade_id de format différent par marché
- **Symptôme** — LINEAR = UUID, SPOT = numérique → risque de collision de clé d'archive.
- **Cause** — Conventions distinctes des deux marchés Bybit.
- **Solution** — trade_id stocké en string → PK `(market, symbol, trade_id)` valable dans les deux cas.
