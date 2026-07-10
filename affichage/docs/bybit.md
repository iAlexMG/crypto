# Bybit — caractéristiques & particularités

> Même format que [docs/bitget.md](bitget.md). Bybit est normalisé vers le format
> **Bitget** (référence). Le rendu (footprint + orderflow) est **identique** : seul
> le connecteur change.
>
> Fichiers : [backend/connectors/bybit.py](../backend/connectors/bybit.py),
> [backend/connectors/bybit_rest.py](../backend/connectors/bybit_rest.py),
> [gui/service.py](../gui/service.py).

---

## 0. Vue d'ensemble

| Élément              | Bybit (API v5)                                               |
|----------------------|-------------------------------------------------------------|
| Marchés              | **SPOT** et **LINEAR** (USDT perp = FUTURES) — captés en continu |
| Init orderbook       | **100 % WS** (`type=snapshot` puis `type=delta`) — comme Bitget |
| Intégrité orderbook  | **numéro de séquence `u`** (`u == last_u+1`) — PAS de checksum |
| Init trades          | WS live (`publicTrade`) ; REST = **récent seulement**       |
| Historique trades    | ⚠ **AUCUN historique profond** : `recent-trade` ≤1000 (spot ≤60), **sans pagination** |
| Historique orderbook | Impossible (comme tous) → heatmap historique = session       |
| Auth                 | Aucune (données de marché publiques)                        |

**Différence clé / LIMITE majeure** : contrairement à Bitget (~90 j) et Binance
(jusqu'au listing), Bybit **n'expose pas d'historique de trades paginable**. L'endpoint
`/v5/market/recent-trade` ne renvoie que les trades récents, sans `startTime` ni
`idLessThan`. Conséquences :
- **pas de backfill profond** (PHASE B du collecteur quasi inopérante) ;
- **un trou profond ne se comble pas** (pas de data REST) → marqué *settled* ;
- l'historique des trades Bybit **grandit vers l'avant** (sessions où l'app tourne),
  comme le carnet/heatmap. Pour de l'historique profond : export CSV du site Bybit.

Clés de flux dans le hub : `bybit` (FUTURES/linear), `bybit_spot` (SPOT).

---

## 1. Connexion (technique)

- **Endpoints WS** :
  - SPOT   : `wss://stream.bybit.com/v5/public/spot`
  - LINEAR : `wss://stream.bybit.com/v5/public/linear`
- **Endpoint REST** : `https://api.bybit.com` (`/v5/market/recent-trade`)
- **Souscription** : `{"op":"subscribe","args":["orderbook.200.BTCUSDT","publicTrade.BTCUSDT", ...]}`
- **Heartbeat** : ping **applicatif** `{"op":"ping"}` toutes les ~20 s (le serveur
  répond `{"op":"pong"}`). `ping_interval=None` côté lib (on gère nous-mêmes), comme Bitget.
- **Watchdog** : reconnexion si aucun message 30 s, aucun trade 120 s, ou session > 30 min.

---

## 2. Orderbook (`orderbook.{depth}.{symbol}`)

- Profondeurs Bybit fixes : 1 / 50 / **200** / 1000. On souscrit **200** (@100 ms) —
  bon compromis profondeur/fréquence (constante `WS_DEPTH`). On pousse ensuite les
  `depth=150` meilleurs niveaux vers le hub (aligné Binance/Bitget).
- **`type=snapshot`** → on **reset** le carnet local puis on applique (carnet complet).
  Bybit peut renvoyer un snapshot spontané (redémarrage de service) → on reset aussi.
- **`type=delta`** → application incrémentale. Taille **`"0"` = suppression** du niveau.
- **Intégrité par séquence `u`** (pas de checksum) :
  - `u <= last_u` → doublon/ancien → ignoré ;
  - `u != last_u+1` → **TROU** → `raise` → reconnexion (le WS renvoie un snapshot frais).
- Prix/tailles en **strings** (parse float). `ts` = champ `ts` du message (ms).

---

## 3. Trades (`publicTrade.{symbol}`)

- Champs : `p` (prix), `v` (taille), `S` (**côté agresseur** : `Buy`/`Sell`),
  `T` (ts ms), `i` (trade id), `seq`.
- **`S` = côté du TAKER** : `Buy` → un acheteur agressif a tapé l'**ask** → `side="buy"` ;
  `Sell` → `side="sell"`. (Mapping direct, pas d'inversion type `isBuyerMaker`.)
- **trade_id** : `i`. Format **différent par marché** : LINEAR = UUID
  (`c7ca4c44-…`), SPOT = numérique (`229000…`). Stocké en **string** → PK archive
  `(market, symbol, trade_id)` OK dans les deux cas.

---

## 4. Historique des trades — REST (LIMITÉ)

- `GET /v5/market/recent-trade?category={linear|spot}&symbol=…&limit={1000|60}`.
- Renvoie `result.list` (trades récents). **Pas de pagination** → on ne peut fournir
  que le paquet récent.
- Adaptateurs ([bybit_rest.py](../backend/connectors/bybit_rest.py)) :
  - `fetch_since(category, sym, start_ms)` → trades récents filtrés `ts >= start_ms` ;
  - `fetch_before(category, sym, before_id)` → renvoie le paquet récent (le collecteur
    ne garde que les trades **dans le trou** par filtre ts, et s'arrête dès qu'une passe
    ne progresse plus → trou profond = non comblable, attendu) ;
  - **pas de `fetch_range`** (seek par temps impossible) → `fetch_range_for` = `None`.
- Mapping REST : `price`, `size`, `side` (`Buy`/`Sell`), `time` (ms), `execId` (= id).

---

## 5. Pièges connus / à retenir

- **Pas d'historique profond** (cf. §0) : ne pas s'attendre à voir un vieux trou se
  combler ; seuls les petits trous récents (dans la fenêtre `recent-trade`) le peuvent.
- **Spot plafonné à 60 trades** par appel REST (linear : 1000).
- **Ping applicatif obligatoire** (`{"op":"ping"}`) sinon coupure.
- **Reset sur snapshot spontané** : Bybit renvoie parfois un snapshot en cours de flux
  → toujours reset le carnet local sur `type=snapshot`.
- **`S` est déjà le côté agresseur** (ne pas inverser comme le `m`/`isBuyerMaker` Binance).
