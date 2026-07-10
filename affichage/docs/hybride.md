# Vue HYBRIDE — conception (aboutissement du projet)

> Document de **préparation**. Objectif final : un orderflow **hybride** regroupant
> tous les exchanges, **normalisé vers Bitget** (référence), + détection d'arbitrage.
> Fiches connecteurs : [bitget](bitget.md), [binance](binance.md), [bybit](bybit.md),
> [okx](okx.md).

---

## 0. Décisions cadrées (validées 2026-06-26)

| Sujet | Décision |
|-------|----------|
| **Alignement des prix** | **Recalage vers Bitget** : chaque exchange est décalé par le spread des mids (`mid_bitget − mid_exchange`) → tout se superpose sur l'axe de prix Bitget. |
| **Périmètre** | **Futures et Spot SÉPARÉS** : un *hybride-Futures* (tous les perp) et un *hybride-Spot* (tous les spot). On garde le sélecteur Marché actuel. |
| **Ordre de construction** | **(1) Footprint hybride** → (2) Heatmap + DOM hybride → (3) Arbitrage. |
| **Arbitrage** | **Live**, net après frais taker par exchange, seuil configurable, **table de frais éditable**. |

**Décisions complémentaires (validées 2026-06-27) :**
- **Sélection des exchanges = légende interactive DANS le graphe** : un petit overlay
  (style légende, en coin du plot) avec un **toggle par exchange** → on choisit en direct
  quels flux entrent dans l'hybride, recalcul à la volée. (Pas de cases dans un menu.)
- **Lignes bid/ask hybrides = celles de Bitget** (la référence). (Pas de mid combiné.)
- **Footprint hybride = un seul total fusionné** (volumes sommés, sans ventilation par
  exchange). La provenance par couleur reste une amélioration ultérieure éventuelle.

**Défauts de 2e ordre (à confirmer, modifiables) :**
- La vue hybride est un **nouvel « exchange » dans le menu Exchange** : `Hybride`. Le menu
  Marché (Futures/Spot) continue de choisir le périmètre. Les exchanges restent
  sélectionnables individuellement comme aujourd'hui.
- **Bitget = référence du prix.** Si le flux Bitget du marché choisi est absent/coupé →
  repli : pas de recalage (prix bruts) + avertissement. (Bitget est le seul tradé.)
- **Footprint hybride = somme des volumes** bid/ask de tous les flux (après recalage).
  La **provenance par exchange** (couleur/ventilation) = amélioration ultérieure ; le
  **DOM** hybride, lui, montre la provenance par niveau dès la phase 2.

---

## 1. Normalisation du PRIX vers Bitget (le cœur du recalage)

Les exchanges ne tradent pas au même prix absolu (quelques $ d'écart ; basis perp/spot,
liquidité, frais). Pour superposer leur orderflow sur l'axe Bitget, on décale chaque
exchange `e` d'un **spread** `s_e = mid_bitget − mid_e`, puis on agrège.

- **Live (zoom serré)** : ⚠ **PAS le spread des mids de carnet** (corrigé 2026-06-27). Mesuré :
  le spread des mids instantanés est **biaisé par le lag asynchrone entre carnets** (un carnet
  se met à jour avant l'autre pendant un mouvement) → il donnait −2 à −3 $ alors que le vrai
  basis ≈ +1,4 $, ce qui décalait Binance d'un cran entier à tick grossier (bug visible). On
  estime donc le basis sur les **prix TRADÉS par APPARIEMENT TEMPOREL** : chaque trade du flux
  est apparié au trade Bitget le plus proche dans le temps (≤ `BASIS_MATCH_MS`=500 ms) et on
  prend la **médiane** des écarts sur `BASIS_WINDOW_S` (=60 s). L'appariement annule le timing
  → vrai offset de prix. Bitget : `s=0`. (`_update_spreads`, `_matched_basis_buckets` dans
  `orderflow_view.py`.)
  > ⚠ **Basis FIGÉ PAR BUCKET de 60 s** (corrigé 2026-06-27) : le basis n'est PAS un scalaire
  > global appliqué à toute la fenêtre. Bug précédent : un seul offset live recalculé ~1 Hz et
  > appliqué à TOUTES les bougies/trades du flux → comme `drow=round(s/tick)` bascule d'un rang
  > entier sur un frémissement de la médiane, **tout le footprint Binance sautait de position**,
  > et un trade ancien recevait l'offset COURANT (mauvais alignement). Corrigé : carte
  > `{bucket60: offset}` (`self._basis_b`) ; **chaque bucket est calculé UNE seule fois puis gelé
  > pour toujours** ; seul le bucket courant (en formation) est rafraîchi. Chaque trade est
  > recalé par le basis FIGÉ de SON bucket (`_shift_trades`, décalage de prix par trade AVANT
  > `build_candles`, plus de `drow` global). Les données originales du hub ne sont jamais mutées
  > (`replace`). Cohérent avec le chemin historique (rollup), déjà par-bucket. Backfill unique
  > d'une fenêtre 1 h (`BASIS_BACKFILL_S`) au 1er passage / après un toggle ; ensuite ~2 buckets
  > seulement (coût borné sur le thread Qt).
  > ⚠ La **dispersion trade-à-trade** restante est large (mesuré p10..p90 ≈ −1 à +5,5 $) : c'est
  > de la **microstructure RÉELLE** (bid-ask bounce, lead-lag entre venues pendant les mouvements),
  > **irréductible** par un offset constant. Le **footprint agrégé** s'aligne (médiane corrigée) ;
  > le **scatter** (trades individuels) montre forcément cette dispersion.
  > ⚠ **DÉCOUPLAGE ancre / référence (2026-06-27)** : l'axe affiché reste **Bitget** (référence),
  > mais le basis est estimé contre le flux le **PLUS LIQUIDE** (ANCRE = **Binance** par défaut),
  > car **Bitget est trop peu tradé** pour un appariement fiable. Mesuré (FUTURES, 30 s) : Bitget
  > **17 trades** vs Binance **222** ; taux d'appariement des autres flux **vs Bitget** = KuCoin
  > **11 %**, OKX 53 % (basis bruité, « transformation pas propre ») **vs Binance** = 89–98 %.
  > On calcule donc `offset(F) = basis(F↔ancre) − basis(Bitget↔ancre)`, les deux médianes contre
  > l'ancre dense ; **F=Bitget → 0** (l'axe ne bouge pas). `_update_spreads`, `_anchor_key`
  > (Binance, sinon le flux actif le plus tradé), `_oref_at` (carry-forward, Bitget épars).
  > ⚠ **GEL PAR TRADE (2026-06-27)** : même avec l'ancre dense, le basis du bucket COURANT se
  > rafraîchit en se formant → les trades de la dernière minute bougeaient encore. Corrigé : le
  > prix recalé de CHAQUE trade est calculé **une seule fois à sa 1ère vue** puis mis en cache
  > par `trade_id` (`self._shift_cache`, dans `_shift_trades`) → un trade déjà affiché ne rebouge
  > JAMAIS ; seuls les NOUVEAUX trades prennent le basis courant. Cache borné (200k, élagué aux
  > trades courants). Footprint ET scatter passent par `_shift_trades` → cohérents et figés.
- **Historique (zoom arrière)** : **priorité au basis TRADE-MATCHÉ figé du live** (unifié le
  2026-06-29, revue #6). La GUI émet un token immuable (`_hybrid_basis_token`) des offsets
  figés du live (`_basis_b`) pour les buckets de la fenêtre → passé en 4ᵉ élément du `market`
  → `aggregate_footprint_rollup_hybrid(..., basis_by_feed)`. Là où ce basis existe (≈ dernière
  heure, qui COUVRE la couture AGG_SPAN_S 30 min), on l'utilise → **même recalage que le live,
  plus de saut de rang à la couture**. **Repli** pour l'historique profond (hors couverture du
  live, où il n'y a de toute façon pas de footprint live à raccorder) : spread estimé depuis le
  rollup, `mid_e(bucket) = (px_ask + px_bid)/2` (repli `px_close`), `s_e = mid_bitget − mid_e`.
  Aucune nouvelle collecte requise.
- **Application** : décaler le prix d'un exchange = décaler ses **lignes de prix** du
  footprint de `round(s_e / base_tick)` rangs avant de sommer dans la grille unifiée.

> ⚠ Le recalage est une **estimation** (le « vrai » prix Bitget équivalent n'existe pas).
> C'est voulu : il fusionne l'orderflow comme s'il était Bitget. Le mode « prix bruts »
> (non retenu) reste possible plus tard pour inspecter le basis.

---

## 2. Périmètre : deux hybrides séparés

| Hybride | Flux agrégés (`market_tag`) |
|---------|-----------------------------|
| **Futures** | `USDT-FUTURES` (Bitget), `binance-futures`, `bybit-linear`, `okx-swap` |
| **Spot**    | `SPOT` (Bitget), `binance-spot`, `bybit-spot`, `okx-spot` |

On ne mélange jamais Spot et Futures (basis/funding structurel). Le menu **Marché**
choisit lequel afficher, comme aujourd'hui.

---

## 3. Architecture proposée (où ça se branche)

L'hybride réutilise l'existant ; **rien de nouveau côté collecte** (les 8 flux sont déjà
captés, archivés, rollupés). Le travail est surtout en **lecture/agrégation + rendu**.

### 3.1 Footprint hybride (Phase 1) — faisable à 100 %, live ET historique
- **Zoom arrière** : pour chaque flux du périmètre, lire son rollup
  (`aggregate_footprint_rollup`), **décaler les rangs de prix** de `s_e(bucket)`, puis
  **sommer** les volumes bid/ask dans la grille unifiée (temps × prix). Bitget non décalé.
  Réutilise `footprint_reader` (hors thread Qt, debounce) → coût borné, pas de gel.
- **Zoom serré** : sommer les `last_trades` de tous les flux (recalés par `s_e` live).
- **Option perf ultérieure** : un **rollup hybride pré-agrégé** (mais le recalage varie
  dans le temps → préférer l'agrégation à la LECTURE d'abord ; pré-calcul si besoin).
- **Lignes bid/ask hybrides** : mid hybride (ex. moyenne pondérée par volume des mids
  recalés), ou simplement les bid/ask Bitget (référence). À trancher en phase 1.

### 3.2 Heatmap + DOM hybride (Phase 2) — live OK, historique limité
- **Live** : fusionner les carnets du hub (tous les flux du périmètre), **prix recalés**,
  **liquidité sommée par niveau + provenance** (quel exchange contribue). DOM latéral.
- **[FAIT — 2a, DOM hybride live, 2026-06-27]** `_hybrid_book_binned` (orderflow_view.py) :
  carnets de tous les flux ACTIFS recalés par le basis du **bucket courant** (`_basis_b[key][cur]`,
  cohérent avec le footprint), binnés sur la **grille de prix commune** (axe Bitget), **SOMMÉS
  en UN SEUL total cumulé par niveau** (couleurs bid/ask classiques vert/rouge, comme le DOM
  mono — pas de ventilation/couleur par exchange). Toggle d'un exchange → sa liquidité s'ajoute/
  se retire du total. Validé headless (recalage + bin + somme exacts : une seule barre/côté,
  bid total = somme des flux par niveau). ⚠ Décision (2026-06-27) : **PAS de provenance colorée
  par exchange** (essayée puis rejetée par l'utilisateur — un seul carnet cumulé).
  - **ANTI-CROISEMENT (2026-06-27)** : en sommant des carnets de venues différentes, leurs
    meilleurs bid/ask diffèrent → après recalage l'ask d'une venue pouvait tomber côté bid d'une
    autre (et inversement) → des bids apparaissaient côté ask. Corrigé : **frontière unique = le
    mid de référence (Bitget)** ; chaque flux ne contribue ses **bids que sous le mid** et ses
    **asks qu'au-dessus**. Les niveaux qui croiseraient (locked/arbitrage) sont écartés de
    l'affichage (relèvent de la Phase 3). Validé headless (0 bid au-dessus du mid, 0 ask en-dessous).
- **[FAIT — 2b, heatmap hybride live, 2026-06-27]** `_draw_heatmap_hybrid` (orderflow_view.py) :
  tous les flux sont déjà échantillonnés en mémoire (`app._stores`, un `HistoryStore` par flux)
  → passés à la vue via `set_hybrid(..., stores)`. Pour chaque flux ACTIF on binne ses colonnes
  de carnet **recalées par le basis FIGÉ du bucket de chaque colonne** (last-wins par bin temps),
  puis on **SOMME les flux**. La heatmap est une **intensité de liquidité** (pas de séparation
  bid/ask) → aucun croisement à gérer. Les lignes bid/ask restent la **référence Bitget**.
  Validé headless (somme recalée exacte = 9 ; toggle → 5). ⚠ **LIMITE** : couvre le **live ~2 h
  en mémoire** ; l'historique PROFOND (disque, `books.db` multi-flux + basis du rollup) reste à
  faire. Au-delà de la mémoire la heatmap hybride est sombre (le footprint, lui, vient du rollup).
  - **Phase 2 (live) FAITE** (DOM cumulé + heatmap sommée). **RESTE** : fusion DISQUE (histo
    profond) ; puis **Phase 3 — arbitrage**.
- **Historique (heatmap)** : limité — `books.db` est forward-only et **Bybit n'a aucun
  historique de carnet**. La heatmap hybride historique ne couvrira que les sessions
  enregistrées, par exchange. Acceptable (déjà la limite mono-exchange).

### 3.3 Arbitrage (Phase 3) — live
- Sur les carnets live du périmètre : `arb = best_bid(X) − best_ask(Y) − fee_taker(X) −
  fee_taker(Y)` (sur les prix **bruts**, pas recalés — l'arbitrage est un écart RÉEL).
- Si `arb > seuil` (configurable) → **signal** (marqueur sur le graphe + éventuelle liste).

- **[FAIT — 3a, moteur + log + barre d'état, 2026-06-27]** `gui/arbitrage.py` : `find_arbs(books,
  fees_bps, threshold_bps, ts)` teste **toutes les paires ordonnées** (vendre au best bid de X,
  acheter au best ask de Y) sur **prix BRUTS** ; `net_bps = edge/mid·1e4 − fee_X − fee_Y`.
  **Seuil = bps NET après frais** (`ARB_THRESHOLD_BPS`, défaut 1 bps), avec l'équivalent $/unité
  affiché. La meilleure opportunité est montrée dans la **barre d'état** (`_run_arbitrage`,
  `_update_title`, hybride seulement) ; **toutes** sont **journalisées** dans `data/arb.db`
  (`backend/arb_archive.py`, throttle 1/s par paire, purge par la rétention 7 j). Validé headless
  (sens/edge/net exacts, throttle, log, retour à zéro en marché normal).
- **Frais taker par défaut** (palier STANDARD VIP 0, sans réduction token ; sources publiques
  2026 ; **éditables**, `TAKER_FEES_BPS`) :

  | Exchange | Taker Futures | Taker Spot |
  |----------|---------------|------------|
  | Bitget   | 0,06 % (6 bps)   | 0,10 % |
  | Binance  | 0,05 % (5 bps)   | 0,10 % |
  | Bybit    | 0,055 % (5,5 bps)| 0,10 % |
  | OKX      | 0,05 % (5 bps)   | 0,10 % |
  | KuCoin   | 0,06 % (6 bps)   | 0,10 % |

- **[FAIT — 3b, marqueurs + table éditable, 2026-06-27]** **Marqueurs** : étoiles dorées
  (`arb_marks`, ScatterPlotItem Z=100) au **mid de chaque opportunité** journalisée, bornées à
  la dernière heure + fenêtre visible (`_draw_arb_markers`). **Table éditable** : bouton
  « Dislocation ⚙ » (barre d'outils ; renommé 2026-06-29) → `ArbSettingsDialog`
  (`gui/arb_settings.py`) édite les frais taker par exchange×marché, le seuil ET le **notional
  mini**. La vue porte une **copie MUTABLE** (`self.fees`, `self.arb_threshold`,
  `self.arb_min_notional`) passée à `find_arbs` → effet au prochain refresh (la table module
  `TAKER_FEES_BPS` reste le défaut). Validé headless (marqueur au mid ; éditer frais/seuil met à
  jour la détection ; vide hors hybride). **Phase 3 COMPLÈTE** (recadrée « dislocation », revue #5).

---

## 4. Données disponibles vs limites

| Brique | Dispo | Limite |
|--------|-------|--------|
| Trades de tous les flux (footprint) | ✅ rollup + live | Bybit : pas d'historique profond → forward only |
| Spread `s_e` historique | ✅ depuis `px_ask`/`px_bid` du rollup | estimation 60 s (pas tick-par-tick) |
| Carnets live (DOM/arbitrage) | ✅ hub (tous les flux) | — |
| Heatmap carnet historique | ⚠ `books.db` forward-only | Bybit : aucun historique carnet |

---

## 5. Plan d'exécution

Phase 1 menée **exchange par exchange** (on valide le recalage à chaque ajout) :

1. **Phase 1 — Footprint hybride** (le cœur) :
   - **[FAIT — étape 1, 2026-06-27]** Scaffold : entrée `Hybride` au menu Exchange
     (Futures/Spot) ; **mode hybride** dans la vue ; **légende-overlay interactive** dans
     le graphe (case par exchange, coin haut-gauche). Périmètre = **Bitget seul** → rendu
     **identique à Bitget** (plomberie + légende validées headless + screenshot, 0 erreur).
     Ancrage : `gui/service.py` (`HYBRID_EXCHANGES`, `hybrid_feeds`, `hybrid_base`),
     `gui/app.py` (`_apply_hybrid`), `gui/orderflow_view.py` (`set_hybrid`, `_build_legend`).
     Couches non encore hybrides (heatmap/DOM/bid-ask) ancrées sur la référence Bitget.
   - **[FAIT — étape 2, Binance, 2026-06-27]** spread `s_e` calculé : **live** = EMA
     (`SPREAD_EMA`) de `mid_bitget − mid_binance` (`_update_spreads`) ; **historique** =
     par bucket depuis `px_ask/px_bid` du rollup (`archive.aggregate_footprint_rollup_hybrid`,
     `_mids_by_bucket`). Agrégation recalée : **live** = bougies par flux décalées de
     `round(s_e/tick)` rangs puis sommées (`candles.merge_hybrid_candles`,
     `FootprintLayer.refresh_hybrid`) ; **zoom arrière** = somme des rollups décalés (clé
     `FootprintReader` = tuple `(base_tag, feed_tags)`). Bid/ask = Bitget ; footprint =
     total fusionné. Scatter live = trades des deux flux recalés. `"Binance"` ajouté à
     `HYBRID_EXCHANGES`. **Validé** : merge synthétique exact ; live volume hybride =
     somme exacte ; Qt toggle Binance ON/OFF (vol 35→5), zoom arrière sans erreur, screenshots.
     ⚠ **Correctif basis (2026-06-27)** : 1ère version recalait sur le spread des **mids** →
     décalait Binance à cause du **lag asynchrone des carnets**. Corrigé : basis estimé sur les
     **prix tradés par appariement temporel** (médiane des écarts trade↔trade ≤500 ms, §1).
     La dispersion trade-à-trade restante (~±5 $) est de la **microstructure réelle** (le
     footprint agrégé s'aligne ; le scatter individuel la montre forcément). La validation de
     l'agg sur historique PROFOND se fera quand le rollup aura accumulé les deux flux (basis
     historique = prix tradés du rollup).
   - **[FAIT — Bitget décochable, 2026-06-27]** Bitget reste la **référence de prix** (axe,
     recalage, corps OHLC, bid/ask) **même décoché** ; décocher masque seulement **son volume**
     du footprint (les autres restent recalés sur Bitget). Toutes les cases de la légende sont
     donc cochables (Bitget annoté « réf. »). (`merge_hybrid_candles` flag `with_vol` ;
     `aggregate_footprint_rollup_hybrid` param `base_vol`.)
   - **[FAIT — étape 3, Bybit, 2026-06-27]** `"Bybit"` ajouté à `HYBRID_EXCHANGES`
     (`bybit-linear`/`bybit-spot`). Rien d'autre à coder (agrégation générique : le basis
     figé par bucket + le rollup hybride marchent pour tout flux). **Validé** : recalage live
     sain mesuré sur Bitget+Bybit FUTURES (~25 s, basis ≈ **+5,9 $ ≈ 1 bps**, stable, pas
     d'aberration) ; périmètre câblé Futures + Spot.
   - **[FAIT — étape 4, OKX, 2026-06-27]** `"OKX"` ajouté à `HYBRID_EXCHANGES`
     (`okx-swap`/`okx-spot`). **Validé** : recalage live sain sur Bitget+OKX FUTURES
     (~25 s, basis ≈ **−2,9 $ ≈ −0,5 bps**, stable) ; périmètre câblé Futures + Spot.
   - **[FAIT — étape 5, KuCoin, 2026-06-27]** `"KuCoin"` ajouté à `HYBRID_EXCHANGES`
     (`kucoin-futures`/`kucoin-spot`). **Validé** : taille cohérente (multiplicateur contrat
     XBTUSDTM déjà appliqué dans `kucoin.py`) ; recalage live sain mais basis plus marqué
     (≈ **−21 $ ≈ −3,5 bps**, stable) — KuCoin futures peu liquide (venue plus petite), c'est
     son vrai écart, géré par le recalage par bucket. **Phase 1 (footprint) COMPLÈTE** pour
     les 5 exchanges du périmètre.
   - **Coinbase : EXCLU** (spot only + USDT peu liquide ; cf. §6). À reconsidérer seulement
     pour un éventuel hybride-Spot dédié si la liquidité le justifie.
2. **Phase 2 — Heatmap + DOM hybride live** (liquidité sommée + provenance).
3. **Phase 3 — Arbitrage live** (table de frais éditable, seuil, marqueurs).

Chaque étape : validation **mesurée** (headless) puis **visuelle**, comme d'habitude.

---

## 6. Questions encore ouvertes (à régler en début de phase)

- **Frais taker par défaut** par exchange/marché (cf. table §3.3).
- **Lissage du spread live** (fenêtre EMA) — à régler empiriquement.

Tranchées le 2026-06-27 (cf. §0) : sélection des exchanges = **légende interactive dans le
graphe** ; lignes bid/ask = **Bitget** ; footprint = **total fusionné** (pas de ventilation).
