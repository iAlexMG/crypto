# Bitget — caractéristiques & particularités (exchange de RÉFÉRENCE)

> Ce document décrit **exactement** comment Bitget est intégré dans le projet :
> endpoints, canaux, formats, fréquences, pièges. Bitget est l'**exchange de
> référence** : tous les autres exchanges (Binance, KuCoin, …) devront être
> normalisés vers SES conventions. Chaque futur exchange aura son propre document
> du même format dans `docs/` (`docs/binance.md`, …), et l'objectif final
> (vue hybride consolidée + arbitrage) sera décrit dans `docs/hybride.md`.
>
> Fichiers concernés : [backend/connectors/bitget.py](../backend/connectors/bitget.py),
> [backend/connectors/bitget_rest.py](../backend/connectors/bitget_rest.py),
> [backend/connectors/base.py](../backend/connectors/base.py),
> [gui/service.py](../gui/service.py), [gui/backfill.py](../gui/backfill.py).

---

## 0. Vue d'ensemble

| Élément              | Bitget                                                        |
|----------------------|--------------------------------------------------------------|
| Marchés              | **SPOT** et **USDT-FUTURES** — captés **en continu et en même temps** |
| Init orderbook       | **100 % WebSocket** (snapshot + updates + checksum CRC32)     |
| Init trades          | WebSocket (snapshot initial **ignoré**) + REST pour l'historique |
| Historique trades    | REST Fills-History (~90 jours), pagination `idLessThan`       |
| Historique orderbook | **Impossible via REST** → heatmap historique = session seule  |
| Auth                 | **Aucune** (données de marché publiques)                      |

Les deux marchés tournent simultanément sous deux **clés de flux** distinctes dans
le hub : `bitget` (= USDT-FUTURES) et `bitget_spot` (= SPOT). Changer de marché
dans l'UI ne fait que **changer la clé lue** ; aucun connecteur n'est arrêté, donc
le marché non affiché continue d'être collecté/archivé/échantillonné.

---

## 1. Connexion (technique)

- **Endpoint WS public (API v2)** : `wss://ws.bitget.com/v2/ws/public`
  Même endpoint pour SPOT et Futures ; le marché est porté par `instType` dans
  chaque souscription.
- **Endpoint REST** : `https://api.bitget.com`
- `websockets.connect(..., ping_interval=None, max_size=2**22)` :
  - `ping_interval=None` car on gère **nous-mêmes** le heartbeat applicatif Bitget.
  - `max_size=4 Mo` car le snapshot `books` complet est volumineux.

### Heartbeat (ping/pong)
- Bitget **ferme la connexion** si le client n'envoie pas de `ping` au moins
  toutes les 30 s.
- On envoie le **texte** `"ping"` toutes les **20 s** ; le serveur répond `"pong"`.
- Le serveur peut aussi envoyer `"ping"` → on répond `"pong"`.
- Ce sont des **trames texte brutes**, pas du JSON.

### Souscription
```json
{"op": "subscribe", "args": [
  {"instType": "USDT-FUTURES", "channel": "books", "instId": "BTCUSDT"},
  {"instType": "USDT-FUTURES", "channel": "trade", "instId": "BTCUSDT"}
]}
```
`instType` ∈ `USDT-FUTURES` | `SPOT`. On souscrit `books` + `trade` pour chaque
symbole (BTCUSDT, ETHUSDT).

### Reconnexion & robustesse
- **Backoff** exponentiel borné : 1 s → ×2 → plafond 30 s (cf. `base.py`).
- **Watchdog** (tâche dédiée, vérifie toutes les 5 s) force une reconnexion si :
  - aucun **message** depuis **30 s** (connexion morte), OU
  - aucun **trade** depuis **120 s** alors que le book continue (canal trade figé
    — cas réel observé après plusieurs heures), OU
  - session > **30 min** (reconnexion préventive anti-dégradation longue durée).
- À la reconnexion, le book local est ré-amorcé par le prochain `snapshot`.

---

## 2. Orderbook — canal `books`

- **Canal** : `books` = **tous les niveaux** (snapshot + updates incrémentales +
  checksum). **PAS** `books1` / `books5` / `books15` (tronqués).
- **Profondeur poussée vers le hub** : **150 niveaux par côté** (`depth=150`),
  tranchés sur le book local complet (`sorted_bids()[:150]`, `sorted_asks()[:150]`).
  > La profondeur est **configurable par exchange** (le nombre de niveaux
  > influence la fréquence des updates). Pour Bitget on garde le book complet et
  > on tranche à l'affichage.
- **Amorçage** :
  - message `action="snapshot"` → on **vide** le book local puis on applique.
  - message `action="update"` → on applique les deltas. Un niveau de **taille 0**
    = suppression du niveau.
- **Book local en strings** (`price_str → size_str`) : indispensable pour calculer
  un **checksum fidèle** à celui de Bitget (la conversion en float fausserait le
  format).
- **Checksum CRC32** :
  - calculé sur les **25 meilleurs niveaux** de chaque côté,
  - format : `bid1p:bid1s:ask1p:ask1s:bid2p:bid2s:ask2p:ask2s:…` (niveaux manquants
    ignorés), CRC32 puis converti en **entier signé 32 bits**,
  - si `checksum(local) != checksum(message)` → on lève une exception →
    **reconnexion = resynchronisation** (nouveau snapshot propre).
- **Horodatage** : `ts` du message = **horloge de l'exchange** (ms UTC). On NE
  prend PAS l'heure locale (sinon book/trades/heatmap se désalignent).
- **Fréquence** : updates très fréquentes ; le book reste plein à **300 niveaux**
  (150+150) en permanence sur les deux marchés (mesuré).

---

## 3. Orderflow (trades) — canal `trade`

- **Canal** : `trade`.
- **Snapshot initial IGNORÉ** : le premier push (`action="snapshot"`) contient des
  trades **antérieurs** au lancement → on l'ignore **intégralement**
  (`channel == "trade" and action != "snapshot"`). On n'affiche que les trades
  **live** (`action="update"`) à partir du lancement. L'historique antérieur vient
  exclusivement du **REST** (section 4), ce qui évite tout doublon/ambiguïté.
- **Champs d'un trade** : `price`, `size` (quantité en **actif de base**, ex. BTC),
  `side`, `ts` (ms UTC), `tradeId`.
- **Côté / agresseur** : `side` = côté de l'**agresseur** (taker).
  ⚠ **Casse différente** : `buy`/`sell` **minuscule en WS**, `Buy`/`Sell`
  **majuscule en REST** → on normalise toujours en `lower()`.
- **Fréquences mesurées** (indicatif, varie avec le marché) :

  | Marché / paire   | Trades/s | Plus grand trou observé |
  |------------------|----------|-------------------------|
  | Futures BTCUSDT  | ~5,8     | ~7 s                    |
  | Futures ETHUSDT  | ~8,7     | ~5 s                    |
  | SPOT BTCUSDT     | ~1,5     | ~18 s (>30 s **normal**)|
  | SPOT ETHUSDT     | ~1,0     | ~9 s                    |

  > Le **SPOT est intrinsèquement clairsemé** : des trous de >30 s y sont normaux
  > (ce n'est PAS un bug). Le carnet SPOT est aussi ~4× plus large que le Futures
  > (étendue ~250 $ vs ~70 $ sur 150 niveaux), car la liquidité y est plus espacée.

---

## 4. Historique des trades — REST Fills-History

- **Endpoints** :
  - Futures : `GET /api/v2/mix/market/fills-history?productType=USDT-FUTURES`
  - SPOT    : `GET /api/v2/spot/market/fills-history`
- **Pagination** : paramètre `idLessThan` = renvoie les trades **plus anciens** que
  ce `tradeId`. `limit=1000` par page. On remonte jusqu'à une borne temporelle.
- **Profondeur dispo** : ~**90 jours**.
- **Public** (pas d'auth). Succès = `payload.code == "00000"`.
- **Usages dans le projet** :
  - **Backfill initial** : ~**30 min** au lancement, **pour chaque marché**.
  - **Extension à la demande** : en mode Libre, naviguer avant la plage chargée
    étend l'archive vers le passé (marché affiché).
  - **Archivage live continu** : les trades WS des **deux** marchés sont aussi
    écrits sur disque (toutes les 2 s) → couverture continue même pour le marché
    non affiché, et au-delà du buffer mémoire.
- **Dédup** par `tradeId` (le format `tradeId` REST = WS → la fusion archive+live
  est fiable). Clé d'archive SQLite : `(market, symbol, tradeId)`.
- ⚠ **Limite dure** : Bitget REST **ne fournit PAS l'orderbook historique**. Donc
  au-delà de la session courante, l'orderflow historique est **trades-only** :
  pas de heatmap de liquidité reconstituable a posteriori (sauf via un collecteur
  qui persisterait les snapshots de carnet — étape future).

---

## 5. Normalisation vers le format Bitget (schéma unifié)

Bitget étant la référence, son format EST le format cible
([backend/models.py](../backend/models.py)) :

| Champ        | Convention Bitget (= cible du projet)                       |
|--------------|------------------------------------------------------------|
| Symbole      | `BTCUSDT`, `ETHUSDT`                                        |
| Prix / size  | floats ; `size` = quantité en **actif de base** (BTC, ETH) |
| Timestamp    | epoch **ms UTC** (horloge de l'exchange)                   |
| Side (trade) | `buy` / `sell` (minuscule) = côté de l'**agresseur**       |

Les futurs connecteurs devront convertir leurs symboles (ex. `XBT/USD` Kraken),
échelles de prix/volumes, casse du `side` et timestamps vers ce schéma **avant**
de pousser dans le hub.

---

## 6. Pièges à connaître (récapitulatif)

1. **Heartbeat texte** `"ping"`/`"pong"` obligatoire (<30 s) — sinon coupure.
2. **Casse du `side`** : minuscule WS / majuscule REST → toujours `lower()`.
3. **Checksum sur 25 niveaux** uniquement (pas sur les 150) ; au-delà, les niveaux
   profonds ne sont pas validés.
4. **Snapshot du canal `trade` ignoré** (historique via REST seulement).
5. **Tout sur l'horloge de l'exchange** (book.ts, trade.ts), jamais l'heure locale.
6. **Book local en strings** pour un checksum fidèle.
7. **Pas d'orderbook historique en REST** → heatmap = session courante seulement.
8. **SPOT clairsemé** : trous de trades >30 s et carnet large = normal, pas un bug.
