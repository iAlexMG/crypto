# KuCoin — caractéristiques & particularités

> Même format que [docs/bitget.md](bitget.md). KuCoin est normalisé vers le format
> **Bitget** (référence). Le rendu (footprint + orderflow) est **identique** : seul
> le connecteur change.
>
> Fichiers : [backend/connectors/kucoin.py](../backend/connectors/kucoin.py),
> [backend/connectors/kucoin_rest.py](../backend/connectors/kucoin_rest.py),
> [gui/service.py](../gui/service.py).

---

## 0. Vue d'ensemble

| Élément              | KuCoin (Spot + Futures)                                     |
|----------------------|------------------------------------------------------------|
| Marchés              | **SPOT + FUTURES** (perps USDT `XBTUSDTM`/`ETHUSDTM`)        |
| Hosts                | Spot `api.kucoin.com` / Futures `api-futures.kucoin.com` (séparés, **même protocole WS**) |
| Init orderbook       | **100 % WS** : canal `level2Depth50` (push du top-50 complet, **remplacement total**) |
| Intégrité orderbook  | **Aucune séquence nécessaire** : chaque push = carnet frais (≠ incrémental) |
| Init trades          | WS live + REST récent                                       |
| Historique trades    | REST **récent uniquement** (~100 trades, **pas de pagination**) → comme Bybit |
| Historique orderbook | Impossible (comme tous) → heatmap historique = session      |
| Auth                 | Aucune (canaux publics) — mais **token WS à bootstrapper**   |

**`side` = côté du TAKER = AGRESSEUR** (pas d'inversion, contrairement à Coinbase).

**Symboles** : Spot `BTC-USDT` (BTCUSDT ↔ BTC-USDT) ; Futures `XBTUSDTM`/`ETHUSDTM`
(**Bitcoin = XBT** chez KuCoin Futures, suffixe `M` ; BTCUSDT ↔ XBTUSDTM, ETHUSDT ↔
ETHUSDTM). Re-normalisés vers `BTCUSDT` pour le hub/l'archive
([`market_symbol()`](../backend/connectors/kucoin.py)).

**⚠ Tailles Futures en CONTRATS** : le WS et le REST futures donnent la taille en
**nombre de contrats** ; on multiplie par le `multiplier` du contrat pour obtenir la
quantité en actif de base (convention du projet). Valeurs (confirmées en live le
2026-06-27) : `XBTUSDTM` = **0.001 BTC**, `ETHUSDTM` = **0.01 ETH**
([`CONTRACT_MULT`](../backend/connectors/kucoin.py)). En spot, multiplier = 1.

**⚠ Timestamps en NANOSECONDES** (WS et REST) → convertis en ms ([`to_ms()`](../backend/connectors/kucoin.py)).

Clés de flux dans le hub : `kucoin` (FUTURES) et `kucoin_spot` (SPOT).

---

## 1. Connexion (technique)

- **Token WS à bootstrapper** (particularité KuCoin) : pas d'URL WS fixe. On `POST`
  `/api/v1/bullet-public` (public, sans auth) sur le host concerné → `{token,
  instanceServers:[{endpoint, pingInterval}]}`. On se connecte ensuite à
  `wss://<endpoint>?token=<token>&connectId=<uuid>` ([`_fetch_bullet()`](../backend/connectors/kucoin.py),
  appelé via `run_in_executor` pour ne pas bloquer la boucle asyncio partagée).
- **Souscription** : un message `{"id":..,"type":"subscribe","topic":<topic>,"response":true}`
  par topic (plusieurs symboles séparés par `,` dans le topic).
- **Heartbeat APPLICATIF** : le client DOIT envoyer `{"id":..,"type":"ping"}` avant
  l'échéance `pingInterval` (~18 s) sinon la connexion ferme ; le serveur répond
  `{"type":"pong"}`. On ping à ~0.6× l'intervalle (borné 8–18 s).
- **Watchdog** : reconnexion si aucun message 30 s, aucun trade 120 s, **carnet figé 45 s**
  (push `level2` stoppé pendant que les trades continuent), ou session > 30 min.

---

## 2. Orderbook (`level2Depth50`, 50 niveaux, remplacement total)

- Topic : Spot `/spotMarket/level2Depth50:<syms>` ; Futures `/contractMarket/level2Depth50:<syms>`.
- Chaque message = le **top-50 complet** (`bids`/`asks` = listes `[price, size]`),
  poussé ~toutes les 100 ms → on **remplace** le carnet à chaque push (pas de snapshot
  REST, pas de séquence, pas de resync : un push perdu est corrigé au suivant).
- Futures : tailles ×`multiplier` (contrats → actif de base). Spot : tel quel.
- On pousse `depth=150` meilleurs niveaux (KuCoin en fournit 50 → on stocke 50).
- **Compromis** : le canal incrémental `level2` donnerait plus de profondeur, mais ses
  formats **diffèrent entre spot et futures** (objet `changes` vs string `change`) et le
  snapshot REST no-auth est partiel → fragile. `level2Depth50` est **uniforme et robuste**
  (comme Coinbase reste sur le partiel). Palier futur si besoin de profondeur : passer à
  l'incrémental + snapshot + séquence.

---

## 3. Trades

- Topic : Spot `/market/match:<syms>` (subject `trade.l3match`) ; Futures
  `/contractMarket/execution:<syms>` (subject `match`).
- Champs : `price`, `size`, `side` (**taker = agresseur**, `buy`/`sell`), `time` (spot,
  ns) ou `ts` (futures, ns), `tradeId`.
- Futures : `size` ×`multiplier` (contrats → actif de base). PK archive `(market, symbol, trade_id)`.

---

## 4. Historique des trades — REST (RÉCENT seulement, comme Bybit)

- Spot : `GET /api/v1/market/histories?symbol=BTC-USDT` → ~100 trades récents.
- Futures : `GET /api/v1/trade/history?symbol=XBTUSDTM` → ~100 trades récents.
- **Aucune pagination** par temps ni par id → historique **forward only**, trous
  **profonds non comblables** (limite d'API, pas un bug).
- Adaptateurs ([kucoin_rest.py](../backend/connectors/kucoin_rest.py)) :
  - `fetch_since(market, sym, start_ms)` → trades récents ≥ start_ms ;
  - `fetch_before(market, sym, before_id)` → paquet récent (le collecteur filtre par ts) ;
  - **pas de `fetch_range`** (seek par temps absent) → `fetch_range_for` = `None`.
- User-Agent explicite (par prudence, comme OKX/Coinbase).

---

## 5. Pièges connus / à retenir

- **Token WS à bootstrapper** (`bullet-public`) avant toute connexion ; ping applicatif obligatoire.
- **Tailles Futures en CONTRATS** → ×`multiplier` (XBTUSDTM 0.001, ETHUSDTM 0.01) sinon
  sizes 1000× trop grandes. Vérif sanity : une taille BTC futures doit être ~0.00x–x, pas un entier.
- **Timestamps en NANOSECONDES** → ÷1e6 (`to_ms`).
- **Symboles** : Spot `BTC-USDT`, Futures `XBTUSDTM` (**BTC → XBT**) ↔ `BTCUSDT` ; toujours re-normaliser.
- **`side` = TAKER = agresseur** (PAS d'inversion, ≠ Coinbase).
- **Historique REST récent seulement** (comme Bybit) : pas de backfill profond, `fetch_range` = None.
- **Carnet 50 niveaux** (`level2Depth50`, remplacement total) : robuste mais moins profond que les 150 ailleurs.
- KuCoin Futures = perps **USDT** (`isInverse=False`) → **éligible à la vue hybride-Futures** (≠ Coinbase/Kraken).
