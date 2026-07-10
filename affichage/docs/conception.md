# Conception de l'application — choix, embûches, adaptations

> Document **transversal** (≠ fiches par exchange). Il caractérise l'application
> globale : la vision, les décisions de conception structurantes, les **embûches
> rencontrées et leurs solutions**, et les adaptations par exchange. À lire en
> complément de [README.md](../README.md) (architecture fichier par fichier) et
> [REVUE_CRITIQUE.md](../REVUE_CRITIQUE.md) (forces/dettes/bugs/plan d'action).
>
> Dernière mise à jour : **2026-06-29**.

---

## 1. Vision

Agrégateur temps réel multi-exchanges (orderbook + trades), tout **normalisé vers
le format Bitget** (référence), affiché dans un **dashboard natif** (PySide6 +
pyqtgraph) : **un seul graphe** où **footprint** (type Quantower, avant-plan) et
**orderflow** (heatmap + scatter + lignes bid/ask + DOM, style Bookmap) sont
**superposés**. Aboutissement visé : une **vue hybride** consolidée recalée sur
l'axe de prix Bitget + **détection d'arbitrage**.

Exchanges intégrés (2026-06-27) : **Bitget** (réf.), **Binance**, **Bybit**, **OKX**,
**KuCoin** (SPOT + Futures chacun) et **Coinbase** (SPOT seulement). Les flux tournent
**tous en continu et simultanément** ; changer d'exchange/marché dans l'UI ne fait que
changer la **clé lue** (aucun connecteur arrêté).

---

## 2. Choix de conception structurants

- **Bitget = format de référence ET base d'ancrage temporel.** Tous les connecteurs
  normalisent vers le schéma Bitget (`BTCUSDT`, ts ms UTC, `side` = agresseur). Depuis
  2026-06-27, Bitget est aussi le **plancher d'historique commun** : aucun autre
  exchange ne construit d'historique plus ancien que le trade Bitget le plus ancien
  (cf. §5). Rationale : la vue hybride recale tout sur Bitget ; sans plancher commun,
  les exchanges divergeraient en couverture temporelle.

- **Tout en mémoire (hub) + archive SQLite.** Le `MarketHub` tient l'état live
  (orderbooks + buffer de trades) thread-safe. Les trades sont archivés dans
  `trades.db`, les snapshots de carnet dans `books.db` (bases séparées : volumes et
  cycles de vie différents). WAL + `busy_timeout` : écriture par 1 connexion+lock,
  lectures read-only par thread → un agrégat lourd ne bloque pas l'archivage live.

- **Un seul graphe superposé** (pas deux panneaux) : footprint et orderflow partagent
  exactement le même axe temps+prix → alignement parfait. Le DOM est un panneau latéral
  lié en Y.

- **Threads, pas processus.** Tout le travail de fond (collecteur, recorders, readers,
  rollup) est **I/O-bound** (réseau + SQLite) : le GIL est relâché sur les I/O, les
  threads donnent du vrai parallélisme. `multiprocessing` n'apporterait rien (SQLite
  reste mono-écrivain) et ajouterait de la complexité (IPC, connexions séparées).

- **Résolution FIXE** (choisie au menu) : l'auto-coarsening au zoom a été retiré
  (trop de re-bucketing instable). L'utilisateur règle la résolution.

- **Deux régimes de rendu** selon la largeur de fenêtre (`AGG_SPAN_S` = 30 min) :
  zoom serré = trades **bruts** (réactif live) ; zoom arrière = footprint depuis le
  **ROLLUP pré-agrégé** (hors thread Qt + debounce) → coût **borné par l'affichage**,
  pas par le nombre de trades.

---

## 3. Embûches rencontrées & solutions

### Connexion / connecteurs
- **OKX REST 403 Forbidden** : OKX (et Coinbase) **filtrent l'User-Agent par défaut**
  d'`urllib` (`Python-urllib/x`). → envoyer un UA navigateur explicite (`Mozilla/5.0`).
  Symptôme trompeur : ressemblait à une géo-restriction.
- **Binance Futures `@aggTrade` muet** : sur `fstream`, le flux `@aggTrade` ne pousse
  rien ; il faut `@trade` (trades individuels). Et **filtrer les non-MARKET** (X="NA",
  prix 0 = fonds d'assurance/ADL) sinon mèche aberrante (low à 0).
- **Carnet figé non détecté (Binance Futures)** : le watchdog ne surveillait que
  `_last_msg`/`_last_trade` ; une **resync depth bloquée** laissait le carnet gelé
  pendant que les trades continuaient (donc watchdog jamais déclenché). → ajout du
  suivi `_last_book` (gel carnet > 45 s → reco). **PORTÉ sur TOUS les connecteurs WS** :
  Binance + Coinbase d'office, puis Bitget/OKX/Bybit le 2026-06-29 (revue critique #8).
- **Vue ancrée sur le carnet** : le bord droit du Live suivait `hist.t_last` (piloté
  par les échantillons de **carnet**) ; carnet figé → la chandelle EN COURS sortait de
  l'écran. → ancrer sur `max(t_last, dernier trade)`.

### Historique / collecteur
- **`find_holes` O(lignes) = 16 s** sur une grosse archive (après import CSV de 7,2 M
  trades OKX), appelé à chaque passe → collecteur figé. → param `since_ms` ; le scan est
  borné au **plancher Bitget** (et après un reset les tables sont petites).
- **Famine entre exchanges** : la boucle unique traitait les flux en file → le gros
  backlog Bitget retardait OKX indéfiniment dans une session. → **un thread par
  exchange** (serveurs/limites indépendants) ; le flux **affiché** est prioritaire avec
  un budget de comblage élargi.
- **429 Too Many Requests** (Binance seek) : sur erreur réseau/429, le flux fautif est
  mis en **cooldown** (le worker sert ses autres flux entre-temps).
- **Règle des 30 s faillible** : un creux de marché réel (aucun trade) ressemble à un
  trou. → si une requête REST parcourt entièrement un trou sans aucun trade dedans, on
  le marque **vide** (`_empty`) et on ne le retente plus (« ne pas s'acharner »).
- **Bybit sans historique profond** : `recent-trade` n'a pas de pagination par temps/id
  → seuls les trous **récents** se comblent ; le passé profond est non comblable (limite
  d'API, pas un bug). Historique **forward only**.
- **PHASE B (passé profond) : curseur en mémoire** → recommence à chaque session (dette
  connue, à persister).

### Rendu / perf
- **Zoom arrière O(trades)** : reconstruire le footprint depuis les bruts chargeait des
  centaines de milliers de trades **par frame** (~300 ms, gel). → **ROLLUP pré-agrégé**
  (base 60 s × tick × side), lu hors thread Qt + debounce. Refresh ~3-5 ms/frame.
- **Lignes bid/ask dans les trous** : le carnet n'a pas d'historique REST → bid/ask
  **reconstruits** depuis les trades (zoom serré) ou le rollup (zoom arrière), fusionnés
  avec le carnet réel, et **coupés sur les vrais trous** de données (`_line_connect`)
  pour ne pas tracer un segment plat trompeur.
- **Heatmap sans historique REST** : le carnet ne se reconstitue qu'en **enregistrant
  les snapshots live** (1 Hz → `books.db`). L'historique de heatmap ne grandit donc que
  **vers l'avant**, pendant les sessions où l'app tourne.

### Import de données externes
- **CSV OKX** (trades + L2 orderbook 400 niveaux) importables : trades → table `trades`
  (`INSERT OR IGNORE`) ; L2 NDJSON (snapshot + updates, `sz=0` = suppression) →
  reconstruction du carnet, **échantillonné à 1 Hz** (cadence du recorder), top-150 par
  côté, BLOB f64/f32 dans `snapshots`. ⚠ Un gros import gonfle l'archive et ralentit
  `find_holes` (cf. ci-dessus) ; et sous la **stratégie de plancher Bitget**, les jours
  importés plus anciens que Bitget sont **ignorés** tant que Bitget ne les a pas couverts
  → un import est souvent **prématuré** avant que Bitget ait remonté assez loin.

---

## 4. Adaptations par exchange (résumé)

| Exchange | Marchés | Init carnet | Intégrité carnet | `side` trade | Historique REST |
|----------|---------|-------------|------------------|--------------|-----------------|
| Bitget   | F + S   | WS          | checksum CRC32   | = agresseur  | Fills-History ~90 j (`idLessThan`) |
| Binance  | F + S   | REST+WS     | séquence U/u/pu  | `m`=maker → inverse | aggTrades + **seek par temps** (startTime) |
| Bybit    | F + S   | WS          | séquence `u`     | `S`=agresseur | recent-trade **récent seulement** (forward only) |
| OKX      | F + S   | WS          | séquence seqId/prevSeqId | = agresseur | history-trades paginable (`after`), **UA requis** |
| Coinbase | **S**   | WS          | **aucune** (level2_batch) | **maker → inverse** | /trades paginable (`after`), **UA requis** |
| KuCoin   | F + S   | WS (`level2Depth50`, remplacement total) | aucune (push complet) | = agresseur (taker) | recent-trade **récent seulement** (forward only) |

KuCoin : **token WS à bootstrapper** (`bullet-public`) + ping applicatif ; **ts en ns** ;
**tailles futures en contrats × multiplier** (XBTUSDTM 0.001, ETHUSDTM 0.01) ; symboles
spot `BTC-USDT` / futures `XBTUSDTM` (**BTC→XBT**). Détails et pièges complets : `docs/<exchange>.md`.

OKX : ⚠ **tailles SWAP en CONTRATS × ctVal** — BTC-USDT-SWAP `ctVal`=0,01 BTC, ETH-USDT-SWAP=0,1 ETH
(`CONTRACT_VAL`, spot=1). Sans ça, le volume OKX était 100× (BTC) / 10× (ETH) → footprint hybride
dominé + points scatter géants. Même piège que KuCoin Futures. Appliqué **sur trades ET carnet** :
LIVE dans `okx.py` (corrigé 2026-06-27) ET **REST dans `okx_rest.py`** (`_mult_for` ; oublié à
l'origine -> archive gonflée, corrigé 2026-06-29, revue critique #1).

---

## 5. Stratégie d'historique (2026-06-27)

1. **Plancher Bitget** : `_floor(sym)` = trade Bitget futures le plus ancien. Bitget
   remonte librement (définit le plancher) ; les autres exchanges **ne descendent jamais
   sous** ce plancher (PHASE B s'arrête au plancher sans se marquer terminé → reprend
   quand Bitget descend). On **ignore** les données plus anciennes que Bitget.
2. **Trous d'abord, passé profond ensuite** (par ligne d'historique `exchange×marché×
   symbole`) : PHASE A (combler tous les trous, du présent au plancher) avant PHASE B.
3. **Seuil de trou = 30 s** sans trade. Règle faillible → trou confirmé vide = plus
   retenté (§3).
4. **Un thread par exchange**, flux affiché prioritaire, cooldown anti-429.

---

## 6. Coinbase & la vue hybride — incompatibilité

Coinbase est **SPOT only** (pas de futures sur l'API publique) et ses paires **USDT**
sont **peu liquides** (qq trades/min sur BTC-USDT, encore moins sur ETH-USDT). Pour la
**vue hybride** (recalage des orderflows sur l'axe de prix Bitget, hybride-Futures et
hybride-Spot séparés), cela pose deux problèmes :
- **pas de futures** → Coinbase ne peut pas participer à l'**hybride-Futures** ;
- **liquidité USDT trop faible** → même en hybride-Spot, son footprint/heatmap est
  clairsemé et son mid bruité, ce qui dégrade le recalage (spread Bitget−Coinbase
  instable) plutôt que de l'enrichir.

→ **Décision** : Coinbase reste disponible en **vue simple** (consultation directe),
mais est **exclu de l'agrégation hybride**. (Si un jour on bascule Coinbase sur ses
paires **USD** beaucoup plus liquides, il faudrait gérer le basis USD/USDT — non prévu.)

---

## 7. Kraken — rejeté (2026-06-27)

Kraken a été **évalué puis écarté** avant tout code. Raisons (par ordre d'importance) :

1. **Spot et Futures = deux API totalement séparées** (≠ OKX, où spot + swap partagent
   l'endpoint v5). Spot = `api.kraken.com` / `wss://ws.kraken.com/v2` (paires `BTC/USDT`,
   carnet à checksum CRC32) ; Futures = `futures.kraken.com` (ex-Crypto Facilities), **WS,
   symboles et protocole entièrement différents**. Intégrer les deux = **doubler** le travail
   (2 connecteurs, 2 clients REST, 2 formats de symboles, 2 schémas d'intégrité) pour un seul
   exchange.
2. **Kraken Futures n'est pas en USDT** : les perps sont marginés **USD/USDC** (symboles type
   `PF_XBTUSD`), pas USDT. Ils **ne mappent pas** sur notre format de référence `BTCUSDT` →
   exactement l'**incompatibilité hybride-Futures déjà identifiée pour Coinbase** (§6). Il
   faudrait gérer un basis USD/USDT — non prévu.
3. **Spot Kraken existe bien en USDT** (`BTC/USDT`, `ETH/USDT`) mais sa **liquidité USDT est
   modeste** face aux majors déjà intégrés (Bitget, Binance, Bybit, OKX). En hybride-Spot, il
   apporterait surtout du bruit au recalage (mid clairsemé) plutôt que de la profondeur — même
   logique que le rejet de Coinbase de l'hybride.
4. **Friction REST supplémentaire** : l'historique spot (`Trades`) pagine **par temps vers
   l'avant** (`since` en nanosecondes), sans pagination « before » native → encore un mode de
   collecteur à écrire (seek par temps, façon Binance), sans bénéfice proportionnel.

→ **Décision** : Kraken **n'est pas intégré**. Le coût (double API séparée) est disproportionné
au regard de l'objectif du projet — une **vue hybride recalée sur Bitget + arbitrage** centrée
sur les gros carnets **USDT/perps**, où Kraken n'ajoute pas de valeur significative. Si le besoin
réapparaît, **Kraken Spot seul** (paires USDT, API cohérente, carnet CRC32 comme Bitget) serait
le point d'entrée le moins coûteux ; les Futures restent hors périmètre tant qu'on raisonne en
USDT. (**KuCoin**, le candidat qui suivait, a lui été **intégré** — Spot + Futures USDT — cf. §4.)

---

## 8. Dette connue / décisions ouvertes

- **PHASE B : curseur en mémoire** → le passé profond recommence à chaque session
  (à persister : un id REST sûr par flux).
- **Rollup mono-résolution** (60 s × tick) : zoom arrière très large (24 h+) ~100 ms ;
  un rollup multi-résolution donnerait ~ms.
- **`find_holes` redevient coûteux** si on réimporte des CSV volumineux récents (le
  plancher ne borne que les non-Bitget ; Bitget lui-même scanne tout son historique).
  Palier futur : suivi **incrémental** des trous.
- **Angle mort watchdog carnet** corrigé sur TOUS les connecteurs WS (Binance/Coinbase
  + Bitget/OKX/Bybit portés le 2026-06-29, revue #8).
- **Legacy** à retirer un jour : `backend/server.py`, `frontend/`.
- **Heatmap hybride** = live ~2 h (mémoire) seulement ; histo profond disque (books.db
  multi-flux) reste à faire (cf. hybride.md 2b-disque).

---

## 9. Vue hybride — COMPLÈTE (2026-06-27)

Les 3 phases sont livrées (détail complet et validations dans `docs/hybride.md`). Décisions
transversales notables :
- **Recalage = 3 protections** : (1) basis **figé par bucket 60 s** ; (2) **découplage
  ancre/référence** — axe affiché = Bitget (réf. produit), mais basis estimé contre le flux
  le **PLUS LIQUIDE** (ancre = Binance), car **Bitget est le moins liquide** (mesuré : 17
  trades/30 s vs 222 ; appariement KuCoin 11 % vs Bitget, 89 % vs Binance) ; (3) **gel par
  trade** (un trade recalé une fois ne rebouge plus). Raison de fond : le choix de la base
  ne change pas l'alignement *relatif* (juste le niveau absolu) mais SA LIQUIDITÉ conditionne
  la qualité de l'estimation → ancrer sur le plus dense.
- **DOM hybride = un seul carnet CUMULÉ** (somme des flux recalés ; provenance colorée par
  exchange essayée puis **rejetée**). Anti-croisement : frontière au mid de référence Bitget.
- **Arbitrage** sur prix BRUTS, seuil en **bps net** après frais ; frais taker publics par
  défaut, **éditables** ; opportunités journalisées (`data/arb.db`).
