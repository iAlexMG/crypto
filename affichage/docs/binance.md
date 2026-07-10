# Binance — caractéristiques & particularités

> Même format que [docs/bitget.md](bitget.md). Binance est normalisé vers le
> format **Bitget** (référence). Le rendu d'affichage (footprint + orderflow) est
> **identique** à Bitget : seul le connecteur change.
>
> Fichiers : [backend/connectors/binance.py](../backend/connectors/binance.py),
> [backend/connectors/binance_rest.py](../backend/connectors/binance_rest.py),
> [gui/service.py](../gui/service.py).

---

## 0. Vue d'ensemble

| Élément              | Binance                                                       |
|----------------------|--------------------------------------------------------------|
| Marchés              | **SPOT** et **USDⓂ FUTURES** (USDT-M) — captés en continu     |
| Init orderbook       | **snapshot REST + diffs WS** raccordées par n° de séquence   |
| Intégrité orderbook  | **numéros de séquence** (`U`/`u`/`pu`) — PAS de checksum      |
| Init trades          | WS live (SPOT `@aggTrade`, FUTURES `@trade`) + REST historique|
| Historique trades    | REST aggTrades, pagination `fromId` (pas de limite 1h)       |
| Historique orderbook | Impossible (comme Bitget) → heatmap historique = session     |
| Auth                 | Aucune (données de marché publiques)                         |

**Différence clé avec Bitget** : Bitget amorce l'orderbook 100 % en WS (snapshot +
updates + checksum) ; Binance exige un **snapshot REST** puis raccorde les diffs WS
par **numéro de séquence** (et resynchronise sur trou de séquence, sans checksum).

Clés de flux dans le hub : `binance` (FUTURES), `binance_spot` (SPOT).

---

## 1. Connexion (technique)

- **Endpoints WS** :
  - SPOT    : `wss://stream.binance.com:9443/ws`
  - FUTURES : `wss://fstream.binance.com/ws`
- **Endpoints REST** :
  - SPOT    : `https://api.binance.com`   (`/api/v3/depth`, `/api/v3/aggTrades`)
  - FUTURES : `https://fapi.binance.com`  (`/fapi/v1/depth`, `/fapi/v1/aggTrades`)
- **Souscription** : méthode **SUBSCRIBE explicite** sur l'endpoint `/ws` (plus
  fiable que l'URL combinée `/stream?streams=...`) :
  `{"method": "SUBSCRIBE", "params": ["btcusdt@depth@100ms", "btcusdt@aggTrade",
  "ethusdt@depth@100ms", "ethusdt@aggTrade"], "id": 1}` (symboles en **minuscule** ;
  sur FUTURES, `@aggTrade` → `@trade`). Le serveur accuse réception par
  `{"result": null, "id": 1}`, puis les events arrivent **non encapsulés** (objet
  brut avec le champ `e` : `depthUpdate` / `aggTrade` (SPOT) / `trade` (FUTURES)).
- **Heartbeat** : géré au niveau **protocole WebSocket** (le serveur envoie des
  `ping`, la lib `websockets` répond `pong` automatiquement). PAS de ping texte
  applicatif (contrairement à Bitget). On laisse `ping_interval=20`.
- **Reconnexion** : backoff exponentiel (base.py) ; **watchdog** identique à Bitget
  (aucun message 30 s / aucun trade 120 s / session > 30 min). À la (re)connexion,
  chaque symbole repart **non synchronisé** → nouveau snapshot.

---

## 2. Orderbook — `<sym>@depth@100ms` (diffs) + snapshot REST

- **Stream** : `<symbol>@depth@100ms` = diffs de profondeur toutes les 100 ms.
- **Profondeur poussée vers le hub** : 150 niveaux/côté (tranchés sur le book local).
- **Snapshot REST** : `/depth?symbol=&limit=1000` → `lastUpdateId` + niveaux.
- **Champs d'un diff** : `U` (1er updateId), `u` (dernier updateId), `b`/`a`
  (bids/asks ; quantité 0 = suppression), `E` (event time). **FUTURES en plus** :
  `pu` (updateId final de l'event précédent) et `T` (transaction time).
- **Algorithme de synchronisation** (implémenté) :
  1. Ouvrir le flux et **bufferiser** les diffs.
  2. Récupérer le snapshot REST (`lastUpdateId`).
  3. Jeter les diffs dont `u < lastUpdateId`.
  4. **Premier diff appliqué** : `U <= lastUpdateId+1` (SPOT) / `U <= lastUpdateId`
     (FUTURES), avec `u >= lastUpdateId`.
  5. **Continuité** : SPOT → `U == last_u + 1` ; FUTURES → `pu == last_u`.
     Sinon → **trou de séquence** → resynchronisation (nouveau snapshot).
  - Robustesse : si le snapshot est **trop ancien** (le buffer commence après) ou
    **trop récent** (aucun diff ne le couvre encore), on attend/refetch → pas de
    boucle de resync.
- **Pas de checksum** : l'intégrité repose entièrement sur la continuité des n°.

---

## 3. Orderflow (trades) — SPOT `@aggTrade`, FUTURES `@trade`

- **Stream** : **SPOT** = `<symbol>@aggTrade` (trades agrégés : fusion des fills
  consécutifs d'un même ordre taker au même prix ; volume total préservé).
  **FUTURES** = `<symbol>@trade` (trades individuels). ⚠ Sur `fstream`, le flux
  `@aggTrade` **ne pousse RIEN** (constaté en direct : 0 event, alors que `@depth`
  et le REST aggTrades fonctionnent) → on prend `@trade`, qui fonctionne.
- **Champs** : `aggTrade` → `a` (aggTradeId) ; `trade` → `t` (tradeId), `X` (type).
  Communs : `p` (price), `q` (qty), `T` (trade time, ms), `m` (**isBuyerMaker**).
- **Côté / agresseur** : `m = true` → l'acheteur est *maker* → l'**agresseur est le
  VENDEUR** → `side = "sell"` ; `m = false` → `side = "buy"`.
- **Symbole** : déjà au format Bitget (`BTCUSDT`) → aucun mapping nécessaire.
- ⚠ **`@trade` futures = trades NON-MARKET parasites** : le flux émet des trades
  `X="NA"` à **prix 0** (fonds d'assurance / ADL). Non filtrés → *low* de bougie à 0
  (**mèche infinie**) + colonne footprint dégénérée (**faux trou**). On ne garde que
  `X == "MARKET"` et `prix > 0` (filtre dans `binance.py::_on_trade`). SPOT
  `@aggTrade` n'a pas de champ `X` → défaut `"MARKET"` → non filtré.
- ⚠ **Dédup live/REST** : le live futures (`@trade`, id `t`) et le REST aggTrades
  (id `a`) ont des espaces d'ids DIFFÉRENTS → la dédup par `trade_id` ne les croise
  pas. Le backfill **partitionne par couverture** (`_live_iv` dans `backfill.py`) :
  on suit les **intervalles réellement couverts par le live** ; le REST (backfill du
  présent + collecteur d'historique) ne réinsère pas les trades tombant dans ces plages
  (anti double-comptage, sinon le volume doublerait), MAIS comble les **creux entre
  intervalles** (coupure/reconnexion live > `LIVE_GAP_TOL_MS`=3s). ⚠ Ne PAS revenir
  à un simple clamp `ts < live_start` : il laisse les coupures live en trous
  permanents (le REST ne peut plus les combler).

---

## 4. Historique des trades — REST aggTrades

- **Endpoints** : SPOT `/api/v3/aggTrades`, FUTURES `/fapi/v1/aggTrades`.
- **Pagination** : `startTime` pour la 1re page puis **`fromId = dernier a + 1`**
  (pas de contrainte de fenêtre 1 h, contrairement à `startTime`+`endTime`).
  `limit=1000`. Public.
- **Usages** (mêmes que Bitget) : backfill initial 30 min/flux + extension à la
  demande + archivage live continu. Dédup par `aggTradeId`. Tag d'archive :
  `binance-futures` / `binance-spot` (colonne `market`).
- ⚠ Comme Bitget, **pas d'orderbook historique** → heatmap = session courante.

---

## 5. Normalisation vers le format Bitget

| Champ        | Source Binance → cible Bitget                                  |
|--------------|---------------------------------------------------------------|
| Symbole      | `BTCUSDT` (identique, aucun mapping)                           |
| Prix / size  | `p` / `q` (qty = actif de base) → floats                      |
| Timestamp    | `T` (ms UTC)                                                   |
| Side (trade) | `m` (isBuyerMaker) → `sell` si true, sinon `buy`              |

---

## 6. Pièges à connaître

1. **Snapshot REST obligatoire** pour amorcer le carnet (≠ Bitget tout-WS).
2. **Continuité de séquence** : SPOT via `U/u`, FUTURES via `pu` → ne pas confondre.
3. **Snapshot décalé** (trop ancien/récent) : attendre/refetch, ne pas boucler.
4. **`m` = isBuyerMaker** : agresseur = vendeur si true (inverse de l'intuition).
5. **Symboles en minuscule** dans les noms de stream WS.
6. **Pas de checksum** : intégrité = séquence uniquement.
7. **FUTURES** : `@aggTrade` est MORT sur `fstream` (0 event) → utiliser `@trade`
   (trades individuels, id `t`). Le SPOT garde `@aggTrade` (id `a`).
8. **`@trade` futures parasites** : trades `X="NA"` à prix 0 (assurance/ADL) →
   filtrer sur `X=="MARKET"` + `prix>0` (sinon mèches infinies + faux trous).
9. **Dédup live/REST futures** : ids `@trade` (`t`) ≠ ids REST aggTrades (`a`) →
   partition par **intervalles de couverture live** (`_live_iv`, `backfill.py`) :
   REST ne recouvre pas les plages live mais comble les creux de coupure. NE PAS
   utiliser un clamp `ts < live_start` (laisse les coupures en trous permanents).
