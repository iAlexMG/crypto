# Coinbase — caractéristiques & particularités

> Même format que [docs/bitget.md](bitget.md). Coinbase est normalisé vers le format
> **Bitget** (référence). Le rendu (footprint + orderflow) est **identique** : seul
> le connecteur change.
>
> Fichiers : [backend/connectors/coinbase.py](../backend/connectors/coinbase.py),
> [backend/connectors/coinbase_rest.py](../backend/connectors/coinbase_rest.py),
> [gui/service.py](../gui/service.py).

---

## 0. Vue d'ensemble

| Élément              | Coinbase (API Exchange, ex-Pro)                            |
|----------------------|------------------------------------------------------------|
| Marchés              | **SPOT uniquement** (pas de futures sur l'API publique)     |
| Init orderbook       | **100 % WS** (`snapshot` puis `l2update`)                   |
| Intégrité orderbook  | **AUCUNE séquence/checksum** sur `level2_batch` → best-effort (resync = snapshot à la reco) |
| Init trades          | WS live (`matches`) + REST historique                      |
| Historique trades    | REST `/trades`, **paginable** par `after`=tradeId (≈ idLessThan) |
| Historique orderbook | Impossible (comme tous) → heatmap historique = session      |
| Auth                 | Aucune (canaux de marché publics)                          |

**⚠ `side` = côté du MAKER** (piège classique Coinbase). L'agresseur est l'**OPPOSÉ** :
un `match` avec `side="sell"` = le maker vendait → un **acheteur** a tapé → on stocke
`side="buy"`. Inversion appliquée à la **fois** en WS et en REST (même famille d'API,
même convention).

**Symboles** : Coinbase utilise `BTC-USDT` (BASE-QUOTE), pas `BTCUSDT`. Le connecteur
mappe le symbole commun ↔ product_id ([`product_id()`](../backend/connectors/coinbase.py))
et re-normalise vers `BTCUSDT` pour le hub/l'archive.

**⚠ Liquidité USDT faible** : les paires *USDT* de Coinbase (BTC-USDT, ETH-USDT) sont
peu actives (qq trades/min sur BTC, encore moins sur ETH) vs leurs paires USD. Le
footprint/heatmap Coinbase sera donc clairsemé, et beaucoup de « trous » de 30 s sont
de **vrais creux** (gérés par la règle « ne pas s'acharner » : trou confirmé vide → plus retenté).

Clé de flux dans le hub : `coinbase_spot` (SPOT). Pas de `coinbase` (futures).

---

## 1. Connexion (technique)

- **Endpoint WS** : `wss://ws-feed.exchange.coinbase.com` (public).
- **Endpoint REST** : `https://api.exchange.coinbase.com` (`/products/{id}/trades`).
- **Souscription** : `{"type":"subscribe","product_ids":["BTC-USDT","ETH-USDT"],"channels":["matches","level2_batch"]}`.
- **Heartbeat** : ping/pong **protocolaire** de la lib websockets (`ping_interval=20`) —
  Coinbase n'exige pas de ping applicatif.
- **Watchdog** : reconnexion si aucun message 30 s, aucun trade 120 s, **carnet figé 45 s**
  (resync `level2` bloquée pendant que les trades continuent), ou session > 30 min.

---

## 2. Orderbook (`level2_batch` → `level2_50`, 50 niveaux)

- `type=snapshot` → **reset** + applique (`bids`/`asks` = listes `[price, size]`).
  `type=l2update` → applique `changes` = `[[side, price, size], …]` ; `side` ∈
  `buy`/`sell`, taille `"0"` = **suppression** du niveau.
- **Pas de numéro de séquence ni de checksum** sur `level2_batch` → intégrité
  **best-effort** : on applique snapshot puis changes ; à la reconnexion on reçoit un
  snapshot frais (le watchdog force la reco en cas de gel). Le canal `level2` complet
  (avec garanties) exige une **authentification** → non utilisé (on reste keyless).
- On pousse les `depth=150` meilleurs niveaux (Coinbase en fournit 50 → on stocke 50).

---

## 3. Trades (`matches`)

- Champs : `price`, `size`, `side` (**côté MAKER**), `time` (ISO 8601), `trade_id`.
- **Inversion** : agresseur = opposé du `side` maker (`sell`→`buy`, `buy`→`sell`).
- `trade_id` = entier (en string). PK archive `(market, symbol, trade_id)`.
- `last_match` (un par produit à la souscription) est **ignoré** (évite un doublon du
  dernier trade) ; les `match` live suivent.

---

## 4. Historique des trades — REST (paginable, support COMPLET)

- `GET /products/{id}/trades?limit=1000&after=<tradeId>` : trades triés du plus
  **récent** au plus ancien ; `after` renvoie les trades **plus anciens** (pagination
  par tradeId décroissant, ≈ `idLessThan` Bitget). `limit` jusqu'à **1000**.
- User-Agent explicite requis (Coinbase filtre l'UA par défaut d'urllib).
- Adaptateurs ([coinbase_rest.py](../backend/connectors/coinbase_rest.py)) :
  - `fetch_since(sym, start_ms)` → remonte jusqu'à start_ms (pagination) ;
  - `fetch_before(sym, before_id)` → un paquet plus ancien que before_id ;
  - **pas de `fetch_range`** (seek par temps absent) → `fetch_range_for` = `None`.
    Le comblage amorce `fetch_before` au bord du trou (id WS == id REST chez Coinbase,
    même API) → support **complet** du collecteur (≠ Bybit).

---

## 5. Pièges connus / à retenir

- **`side` = côté MAKER → INVERSER** pour obtenir l'agresseur (WS et REST).
- **Pas de futures** : Coinbase n'apparaît qu'en **SPOT** ; la GUI bascule auto le menu
  Marché sur Spot si on choisit Coinbase ([`markets_for()`](../gui/service.py)).
- **Liquidité USDT faible** : footprint clairsemé, beaucoup de creux légitimes (≠ trous à combler).
- **level2 sans séquence/checksum** : intégrité best-effort, resync = snapshot (reco).
- **Symboles** `BTC-USDT` ↔ `BTCUSDT` : toujours re-normaliser pour le hub/archive.
- **User-Agent** obligatoire en REST (sinon risque de blocage, comme OKX).
