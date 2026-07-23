# Étude — API de trading Bitget (POC automatisation, ÉTAPE A)

> Écrite le 2026-07-22. Méthode : **mesurer, pas supposer** — doc officielle Bitget API v2
> + une **sonde signée read-only** (`../sonde/sonde_bitget.py`) contre la démo. Ce qui est
> ✅ MESURÉ l'est ; le reste est marqué « à sonder » (clés démo requises).
>
> **Cadrage** (confirmé par l'utilisateur) : POC de la **chaîne d'exécution** (pas la
> rentabilité) ; **signaux sur données Binance → exécution sur Bitget** (pont basis
> trade-matché + garde-fou de dislocation) ; **auto complet** ; **démo Bitget d'abord**.
> Marché retenu : **USDT-M perp**, symbole BTC (miroir des backtests crypto).

## 1. Accès démo (« paptrading ») — l'ambiguïté est TRANCHÉE

La démo Bitget n'est **pas** juste « le compte réel avec un en-tête ». C'est un
**environnement distinct** avec ses **propres symboles**.

- **Clés dédiées** : les créer dans Bitget en **mode Démo → Personal Center → API Key
  Management** (différentes des clés réelles).
- **En-tête** `paptrading: 1` sur chaque requête REST, avec la clé de démo.
- **Même hôte** `https://api.bitget.com` (WebSocket démo : `wss://wspap.bitget.com/v2/ws/...`).
- 🪤 **PIÈGE MESURÉ (sonde signée, 2026-07-22) — deux « démos » à ne pas confondre** :
  - Le **« Futures Trading Simulator » du site** utilise le productType **`SUSDT-FUTURES`**
    (symboles `SBTCSUSDT`/`SETHSUSDT`/`SXRPSUSDT`, préfixe `S`). Ces contrats apparaissent dans
    le **catalogue PUBLIC** (`market/contracts` sans `paptrading`). **MAIS ce n'est PAS la démo
    de l'API.** Sous `paptrading:1` : `SBTCSUSDT` → **40034 « does not exist »**, et
    `all-position SUSDT-FUTURES` avec marge `SUSDT` → **40778 « does not support SUSDT as
    margin »**. Le compte `SUSDT-FUTURES` répond `00000` mais **vide**.
  - **La démo de l'API (`paptrading:1`) tourne sur le produit NORMAL** : **`USDT-FUTURES`**,
    symbole **`BTCUSDT`**, marge **`USDT`**, avec un **compte virtuel** (fonds fictifs). C'est
    la bonne nouvelle : le symbole du signal (BTCUSDT sur Binance) mappe **directement** sur
    `BTCUSDT` Bitget démo. → **On trade `USDT-FUTURES`/`BTCUSDT`/marge `USDT` avec `paptrading:1`.**
  - 🔎 **Confirmation finale en attente** de la sonde durcie (elle teste les combinaisons de
    positions ; la combinaison qui répond `00000` fige le triplet). Le solde du compte virtuel
    (~50 000) dira aussi où vivent les fonds. **`code 40099`** = clé/mode incorrects.

## 2. Signature v2 (implémentée dans la sonde)

En-têtes : `ACCESS-KEY`, `ACCESS-SIGN`, `ACCESS-TIMESTAMP`, `ACCESS-PASSPHRASE`,
`Content-Type: application/json`, `locale`, `paptrading` (démo).

```
ACCESS-SIGN = base64( HMAC_SHA256( secret, timestamp + METHOD + requestPath+query + body ) )
timestamp   = millisecondes epoch (== ACCESS-TIMESTAMP)
METHOD      = "GET"/"POST" en MAJUSCULES
requestPath+query = "/api/v2/…" + "?a=1&b=2" (la MÊME chaîne que l'URL)
body        = corps JSON en string ("" en GET)
```
🪤 La chaîne de query signée doit être **identique** à celle de l'URL (mêmes clés, même
ordre) — la sonde construit la query une seule fois pour les deux. Horloge système à l'heure
(sinon signature rejetée). Le secret et la passphrase ne sont **jamais** journalisés.

## 3. Specs symbole — les CHAMPS qui pilotent le sizing

`GET /api/v2/mix/market/contracts` renvoie, par symbole : `pricePlace` / `priceEndStep`
(→ **tick = priceEndStep × 10⁻ᵖʳⁱᶜᵉᴾˡᵃᶜᵉ**), `minTradeNum` (taille mini en coin de base),
`minTradeUSDT` (notional mini), `sizeMultiplier` (pas de taille), `takerFeeRate`/`makerFeeRate`,
`minLever`/`maxLever`, `fundInterval` (funding perp).

- Exemple **mesuré** sur le symbole du simulateur `SBTCSUSDT` (2026-07-22, montre la forme) :
  tick **0,1**, `minTradeNum` 0,0001, `minTradeUSDT` 5, taker 0,06 %, levier 1–125, funding 8 h.
- 🔎 **Le vrai symbole du POC est `BTCUSDT`** (démo API = produit normal) → **la sonde durcie
  lit ses specs** (endpoint public réel). Sizing : taille en **BTC**, ≥ `minTradeNum`, notional
  ≥ `minTradeUSDT`.

## 4. Checklist des mécanismes — le cahier des charges des étapes B/C

*(Endpoints mix v2. « ✅ » = mesuré ; « 🔎 » = à confirmer par la sonde puis l'étape B.)*

| Mécanisme | Endpoint | Statut |
|---|---|---|
| Lire compte / solde | `GET /api/v2/mix/account/accounts` | 🔎 sonde |
| Lire positions ouvertes | `GET /api/v2/mix/position/all-position` | 🔎 sonde |
| Specs symbole (tick, min, levier) | `GET /api/v2/mix/market/contracts` | ✅ |
| **Ordre market** | `POST /api/v2/mix/order/place-order` | 🔎 B |
| **SL/TP attachés (bracket)** | champs `presetStopLossPrice` / `presetStopSurplusPrice` du place-order | 🔎 B |
| SL/TP « plan » (alternative) | `POST /api/v2/mix/order/place-tpsl-order` | 🔎 B |
| Modifier (stop suiveur) | `POST /api/v2/mix/order/modify-tpsl-order` | 🔎 B |
| Annuler | `POST /api/v2/mix/order/cancel-order` | 🔎 B |
| Fermer / flat (kill switch) | `POST /api/v2/mix/order/close-positions` | 🔎 B |
| Détail ordre / fills | `GET /api/v2/mix/order/detail`, `.../fill-history` | 🔎 B |
| Levier / mode de marge | `POST /api/v2/mix/account/set-leverage`, `set-margin-mode` | 🔎 B |
| Suivi live (positions/ordres) | WS privé `wspap.bitget.com` | 🔎 C |

🪤 **One-way vs hedge** : le champ `tradeSide` (open/close) n'est requis qu'en mode **hedge**.
En **one-way**, `side` (buy/sell) suffit. Le mode du compte gouverne — **à mesurer** avant le
place-order (l'équivalent crypto du « où vit le stop » des indices).

## 5. Le pont Binance → Bitget (réutilisé, PAS réécrit)

- **Recalage basis trade-matché** (doc `../../affichage/docs/hybride.md §1`) : traduit un prix
  Binance en prix **Bitget** par appariement temporel des trades (≤ 500 ms), médiane sur 60 s,
  figé par bucket ; Bitget = ancre. C'est ce qui porte le signal (né sur Binance) sur l'axe de
  prix exécutable Bitget.
- **Garde-fou dislocation** : `../../affichage/gui/arbitrage.py` `find_arbs()` — si les carnets
  divergent au-delà des frais taker, on réduit la taille ou on suspend.
- ⚠️ **Nuance démo** : les symboles démo (`SBTCSUSDT`) ont leur **propre** flux de prix, distinct
  du réel `BTCUSDT`. Le basis Binance↔Bitget se calcule sur le **réel** ; en démo, le SL/TP doit
  s'ancrer sur le **prix démo courant** (lu via `market/contracts`/ticker démo), le basis servant
  à décider (signal) plus qu'à fixer le niveau exact. À trancher au montage du runner (étape C).

## 6. Ce que la démo ne prouve PAS (borne le POC)

Slippage et latence réels, funding réellement débité, profondeur/liquidité du carnet (la démo
n'a que 3 symboles). Le POC prouve la **chaîne** (signal → ordre → bracket → gestion → journal),
pas la fidélité d'exécution. Le passage réel (plus tard) mesurera ça.

## 7. Garde-fous — dès la conception (comme les indices)

- **Garde démo codé en dur** : le client refuse de tourner sur le réel sauf flag explicite
  « autoriser le réel » (analogue du garde anti-compte-réel des indices).
- **Clés jamais lues par Claude** ; `credentials.local.json` gitignoré (gabarit `.example`).
- **Taille max** par position, **kill switch** (flatten `close-positions`), **pause sur
  dislocation**, journal NDJSON (format des indices).

## Prochaines étapes

- ✅ **Sonde** (`../sonde/sonde_bitget.py`) — auth démo prouvée, **triplet figé** :
  `USDT-FUTURES` / `USDT` / `BTCUSDT` (§1). ⚠️ Compte démo à `dispo=0` → **approvisionner**
  (réclamer les fonds virtuels dans l'UI démo) avant tout ordre.
- ✅ **Étape B — client bâti** : `../bitget_trading.py` (place/modify/cancel/close, SL/TP
  attachés, garde démo en dur) + `../placer_ordre_demo.py` (test « 1 ordre + bracket », dry-run
  puis `--go`). **Reste à VALIDER en live** dès que le compte démo est approvisionné (coche les
  🔎 « B » du §4 en une fois).
- **Étape C** : runner POC **SMA cross** (signal Binance → pont basis → ordre + bracket démo →
  journal NDJSON → garde-fous dislocation/kill switch/taille max).

## Sources

- [Demo trading REST](https://www.bitget.com/api-doc/common/demotrading/restapi) ·
  [Signature](https://www.bitget.com/api-doc/common/signature) ·
  [Place Order (mix)](https://www.bitget.com/api-doc/contract/trade/Place-Order) ·
  [TP/SL plan](https://www.bitget.com/api-doc/contract/plan/Place-Tpsl-Order)
- Specs `SBTCSUSDT` / catalogue démo : mesurés sur `GET /api/v2/mix/market/contracts?productType=SUSDT-FUTURES` (2026-07-22).
