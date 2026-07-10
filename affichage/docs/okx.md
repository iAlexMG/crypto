# OKX — caractéristiques & particularités

> Même format que [docs/bitget.md](bitget.md). OKX est normalisé vers le format
> **Bitget** (référence). Le rendu (footprint + orderflow) est **identique** : seul
> le connecteur change.
>
> Fichiers : [backend/connectors/okx.py](../backend/connectors/okx.py),
> [backend/connectors/okx_rest.py](../backend/connectors/okx_rest.py),
> [gui/service.py](../gui/service.py).

---

## 0. Vue d'ensemble

| Élément              | OKX (API v5)                                                |
|----------------------|------------------------------------------------------------|
| Marchés              | **SPOT** et **SWAP** (USDT perp = FUTURES) — captés en continu |
| Init orderbook       | **100 % WS** (`action=snapshot` puis `action=update`) — comme Bitget |
| Intégrité orderbook  | **chaînage de séquence `seqId`/`prevSeqId`** (checksum CRC32 dispo mais NON utilisé) |
| Init trades          | WS live (`trades`) + REST historique                       |
| Historique trades    | REST `history-trades`, **paginable** par `after`=tradeId (≈ idLessThan) |
| Historique orderbook | Impossible (comme tous) → heatmap historique = session      |
| Auth                 | Aucune (données de marché publiques)                       |

**Différence clé / SYMBOLES** : OKX n'utilise PAS `BTCUSDT` mais `BTC-USDT` (spot) /
`BTC-USDT-SWAP` (perp). Le connecteur mappe le symbole commun ↔ instId OKX
([`inst_id()`](../backend/connectors/okx.py)) et **re-normalise vers `BTCUSDT`** pour
le hub/l'archive → cohérent avec les autres exchanges.

**⚠ REST 403 = User-Agent** (et non géo-restriction, contrairement à ce qu'on a cru
d'abord) : OKX **bloque l'User-Agent par défaut d'urllib** → on envoie un UA navigateur
explicite (`Mozilla/5.0`, cf. `okx_rest.py`). Une vraie géo-restriction reste possible
selon le réseau (WS muet / `checksum=0`) — à valider visuellement si les données manquent.

Clés de flux dans le hub : `okx` (FUTURES/swap), `okx_spot` (SPOT).

---

## 1. Connexion (technique)

- **Endpoint WS** : `wss://ws.okx.com:8443/ws/v5/public` (spot + swap, même URL ;
  l'instId porte le marché).
- **Endpoint REST** : `https://www.okx.com` (`/api/v5/market/history-trades`).
- **Souscription** : `{"op":"subscribe","args":[{"channel":"books","instId":"BTC-USDT-SWAP"},{"channel":"trades","instId":"BTC-USDT-SWAP"}, ...]}`.
- **Heartbeat** : ping **texte** `"ping"` toutes les ~20 s (le serveur répond `"pong"`),
  `ping_interval=None` côté lib — identique à Bitget.
- **Watchdog** : reconnexion si aucun message 30 s, aucun trade 120 s, ou session > 30 min.

---

## 2. Orderbook (`books`, 400 niveaux)

- `action=snapshot` → **reset** + applique (carnet complet). `action=update` → applique
  l'incrément. Taille `"0"` = **suppression** du niveau.
- **Entrées à 4 champs** : `[px, sz, "0"(déprécié), nbOrders]` → on lit px/sz.
- **Intégrité par séquence** (testé : chaînage propre) :
  - chaque message porte `seqId` (courant) et `prevSeqId` (précédent) ;
  - un `update` est contigu si **`prevSeqId == dernier seqId`** ;
  - `seqId == dernier` → inchangé (ignoré) ; `seqId < dernier` → ancien (ignoré) ;
  - sinon → **TROU** → `raise` → reconnexion (le WS renvoie un snapshot frais).
- **Checksum CRC32** : OKX le fournit (format `bid:sz:ask:sz…` sur 25 niveaux), mais il
  est **NON utilisé** ici (impossible à vérifier depuis un env géo-restreint qui renvoie
  `checksum=0` ; le `seqId` suffit à garantir l'intégrité).
- On pousse les `depth=150` meilleurs niveaux vers le hub (aligné Binance/Bitget).

---

## 3. Trades (`trades`)

- Champs : `px`, `sz`, `side` (`buy`/`sell` = **côté agresseur**), `ts` (ms), `tradeId`.
- **`side` = côté du TAKER** : `buy` → un acheteur agressif a tapé l'**ask** → `side="buy"`.
  (Mapping direct, pas d'inversion.)
- **trade_id** = `tradeId` (numérique en string). PK archive `(market, symbol, trade_id)`.
- ⚠ **`sz` SWAP = nombre de CONTRATS, pas l'actif de base** (corrigé 2026-06-27). On
  multiplie par `ctVal` pour obtenir la quantité en BTC/ETH : **BTC-USDT-SWAP `ctVal`=0,01 BTC**,
  **ETH-USDT-SWAP `ctVal`=0,1 ETH** (confirmé via `GET /api/v5/public/instruments`). Spot = 1,0
  (déjà en actif de base). Appliqué aux **trades ET au carnet** (`CONTRACT_VAL`, `self._mult`
  dans `okx.py`). Sans ça, le volume OKX était 100× (BTC) / 10× (ETH) trop gros → footprint
  hybride dominé par OKX + points scatter géants. (Même piège que KuCoin Futures, cf. kucoin.md.)

---

## 4. Historique des trades — REST (paginable, support COMPLET)

- `GET /api/v5/market/history-trades?instId=…&type=1&after=<tradeId>&limit=100`.
  `type=1` = pagination par `tradeId` ; `after` = renvoie des trades **plus anciens**
  (équivalent de l'`idLessThan` Bitget). `limit` max **100**.
- Adaptateurs ([okx_rest.py](../backend/connectors/okx_rest.py)) :
  - `fetch_since(market, sym, start_ms)` → remonte jusqu'à start_ms (pagination) ;
  - `fetch_before(market, sym, before_id)` → un paquet plus ancien que before_id ;
  - **pas de `fetch_range`** (seek par temps non câblé) → `fetch_range_for` = `None`.
    Le comblage de trou **amorce `fetch_before` au bord du trou** (l'id WS == l'id REST
    chez OKX → sûr), ce qui suffit. → support **complet** du collecteur (≠ Bybit).

---

## 5. Pièges connus / à retenir

- **Symboles instId** (`BTC-USDT-SWAP`/`BTC-USDT`) : toujours re-normaliser vers le
  symbole commun (`BTCUSDT`) avant de pousser au hub/archive.
- **Intégrité = seqId** (pas checksum ici). `prevSeqId == dernier seqId` sinon resync.
- **`side` déjà = côté agresseur** (ne pas inverser).
- **REST 403 → User-Agent** (UA navigateur requis, cf. okx_rest.py). Si WS muet malgré
  tout → éventuelle géo-restriction réseau.
- **Ping texte** `"ping"` obligatoire (comme Bitget), sinon coupure ~30 s.
- `history-trades` plafonné à **100/page** (vs 1000 Bitget) → comblage plus lent mais complet.
