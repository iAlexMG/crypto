# Crypto — Automatisation (à venir)

Bot Python : **signaux calculés sur les données Binance, exécution automatisée sur Bitget**.

## Architecture décidée (2026-07-11)

- **Décision et exécution séparées** : les signaux naissent sur le marché de référence
  (Binance — la même donnée que le pilier historique et les backtests) ; les ordres
  partent sur l'API de Bitget.
- **Pont hybride** : le recalage basis trade-based construit pour l'affichage traduit en
  continu les prix Binance en prix exécutables Bitget ; la détection de dislocation sert
  de garde-fou (réduction de taille ou arrêt quand les venues divergent ou qu'un flux
  devient suspect).

## Démarche

- Développement et validation sur la **démo Bitget** (fonds virtuels, en-tête `paptrading: 1`,
  endpoints WebSocket dédiés) — même plateforme que la production, sans risque.
- Réutilise le connecteur Bitget déjà présent dans l'affichage
  (`../affichage/backend/connectors/bitget.py`, `bitget_rest.py`).
- Passage en réel une fois les stratégies validées en démo, avec des garde-fous
  d'exécution : taille de position limitée, arrêt d'urgence, journal des ordres.

## État (2026-07-22) — cadrage confirmé + ÉTAPE A en cours

Cadrage confirmé par l'utilisateur : **POC de la chaîne d'exécution** (pas la rentabilité),
**signaux Binance → exécution Bitget** (on garde l'archi ci-dessus), **auto complet**,
**démo Bitget d'abord**. Stratégie de départ : **SMA cross** (= le H1 du pilier indices).

- 📄 **`docs/etude-api-trading-bitget.md`** — étude de l'API trading Bitget v2 (accès démo
  `paptrading`, signature, ordres + SL/TP, specs symbole, checklist des mécanismes = cahier
  des charges des étapes B/C). ✅ Mesuré sans clés : la **démo futures = `SUSDT-FUTURES`**
  (`SBTCSUSDT`/`SETHSUSDT`/`SXRPSUSDT`, marginCoin `SUSDT`, tick 0,1).
- 🔬 **`sonde/sonde_bitget.py`** — sonde SIGNÉE, **read-only** : a **figé le triplet démo**
  (voir ci-dessous).
- 🧰 **`bitget_trading.py`** (étape B) — client de trading signé : compte/positions/specs/ticker,
  levier/mode, **ordre market + SL/TP attachés**, `close_all` (kill switch), `cancel_all`, et le
  **stop suiveur serveur** de H2 (`plan_sl_orderid` + `modify_position_sl` = `modify-tpsl-order`
  sur le `loss_plan`). **🔒 garde démo codée en dur** (refuse le réel sans `allow_real=True`).
- 🧪 **`placer_ordre_demo.py`** — test « 1 ordre + bracket » sur démo. ✅ **VALIDÉ live le
  2026-07-22** : ordre `filled`, bracket SL/TP attaché, position lisible puis refermée.
- 🤖 **`runner_sma.py`** (étape C1) — le RUNNER : **SMA 9/21 cross sur données Binance →
  ordre + bracket sur démo Bitget** (= H1 des indices : SL 1,5×ATR / TP 1R). Mode **SHADOW
  par défaut** (journalise, aucun ordre), `--go` pour exécuter. Kill switch Ctrl-C (flat).
- 🛡️ **`basis_dislocation.py`** (étape C2) — le pont « hybride » léger (REST public) :
  **basis** Binance↔Bitget (journalisé à chaque signal) + **garde-fou de dislocation**
  (carnets croisés net des frais, ou basis anormal → le runner **refuse d'entrer**). Un basis
  stable de quelques $ est normal et ne bloque pas.

**Triplet démo FIGÉ + VALIDÉ (2026-07-22)** : `USDT-FUTURES` / `USDT` / `BTCUSDT` (tick 0,1 ;
mini 0,0001 BTC ; notional ≥ 5 USDT ; levier ≤ 150 ; marge **isolée**). Compte démo
approvisionné (10 000 USDT virtuels, vus par l'API). Le simulateur `SUSDT-FUTURES` du site
N'EST PAS la démo API.

**Prochaine action (utilisateur)** : lancer le runner —
```
python runner_sma.py            # SHADOW : voir les croisements se journaliser (aucun ordre)
python runner_sma.py --go       # exécuter sur la démo
```
✅ **C2 FAIT** (basis + garde-fou dislocation branchés dans le runner).
- 🔁 **`jumeau_sma.py` + `parite_sma.py`** (comme la phase 4 indices) — le jumeau rejoue la
  logique du runner sur l'historique Binance ; la parité compare les signaux (minute + sens)
  du **shadow live** au **jumeau** du même jour, dans les fenêtres où le shadow tournait.
  ✅ **PARITÉ PARFAITE le 2026-07-23** (signal 06:22 long, prix 65677,5 identique des deux
  côtés → 100 %). Même source Binance des deux bords = zéro écart de données.

**POC crypto COMPLET** : chaîne d'exécution prouvée (long+short) + garde-fou + parité 100 %.

⚠️ Clés API **jamais lues/saisies par Claude** (gitignore + gabarit `.example`). Dire
« exchange », pas « venue ».
