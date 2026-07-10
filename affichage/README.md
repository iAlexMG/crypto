# Crypto Aggregator — données de marché multi-exchanges en temps réel

Agrège en temps réel l'orderbook et les trades de plusieurs exchanges crypto,
normalise tout vers le format de référence **Bitget**, et affiche dans un
**dashboard natif** (PySide6 + pyqtgraph) : **un seul graphe** où un **footprint**
type Quantower (en avant-plan) et un **orderflow style Bookmap** (heatmap + scatter
+ DOM) sont **superposés**, avec des couches sélectionnables et configurables.

> État actuel : **étape (d) — VUE HYBRIDE COMPLÈTE**. **Bitget** (référence), **Binance**, **Bybit**,
> **OKX** et **KuCoin** (SPOT + Futures) + **Coinbase** (SPOT seulement) sont implémentés
> de bout en bout, **captés en continu et simultanément**. La **vue hybride** (footprint +
> DOM cumulé + heatmap recalés sur l'axe Bitget) et la **détection de DISLOCATION live** (ex-
> « arbitrage » ; net de frais taker + filtre de taille, table éditable) sont **livrés** — cf.
> [docs/hybride.md](docs/hybride.md). ⚠ **Coinbase est
> exclu de la vue hybride** (pas de futures, paires USDT peu liquides — cf. [docs/conception.md](docs/conception.md)).
> ⚠ **Kraken a été évalué puis rejeté** (Spot/Futures = deux API séparées, futures non-USDT —
> cf. [docs/conception.md](docs/conception.md), §7).

## Lancer (environnement conda dédié)

Première fois — créer l'environnement :

```bash
conda env create -f environment.yml
```

Ensuite, à chaque session — **dashboard natif** :

```bash
conda activate crypto-agg
python run_gui.py
```

La fenêtre s'ouvre en plein écran. Barre du haut : **résolution** (1m/5m/15m/30m/1h/4h/1j ;
**FIXE** = choisie au menu, le zoom ne la change plus), **saut à une date**, **exchange**
(Bitget/Binance/Bybit/OKX/KuCoin/Coinbase + **Hybride**) et **marché** (Futures/Spot ; le menu s'adapte —
Coinbase n'a que Spot) en menus déroulants, **Affichage ⚙** (couches Footprint/Heatmap/Trades/Carnet :
visibilité, opacité, ordre avant/arrière, + options par type), mode **Live**, et la
**paire** (BTC/USDT, ETH/USDT — une seule affichée à la fois). La **devise** ($US/$CA)
est discrète sous l'axe des prix. Fermer la fenêtre (ou **Quitter**) pour arrêter
proprement.

### PyCharm — interpréteur conda
File ▸ Settings ▸ Project ▸ **Python Interpreter** ▸ Add ▸ **Conda Environment**
▸ *Use existing* ▸ `C:\Users\Moi\anaconda3\envs\crypto-agg\python.exe`, puis lancer
`run_gui.py`.

## Architecture

```
backend/
  models.py              schéma unifié (OrderBook, Trade) = format Bitget (ts ms UTC, side buy/sell)
  hub.py                 état marché EN MÉMOIRE, thread-safe (orderbooks + buffer trades 60k)
  archive.py             archive SQLite des trades (orderflow historique), PK(market,symbol,trade_id),
                         mode WAL ; écriture (1 conn+lock) / lectures (conn read-only par thread).
                         + ROLLUP pré-agrégé du footprint (footprint_rollup/_ohlc) maintenu par curseur
                         rowid (rollup_step) ; aggregate_footprint_rollup (zoom arrière) ; purge (rétention)
  book_archive.py        archive SQLite des snapshots de carnet (heatmap historique), base SÉPARÉE
                         (books.db), WAL ; bid/ask par snapshot (mids pour la normalisation hybride) ; purge
  connectors/
    base.py              interface commune + reconnexion/backoff
    bitget.py            Bitget WS (books+trade, checksum CRC32, heartbeat, watchdog)
    bitget_rest.py       Bitget REST Fills-History (~90 jours) ; fetch_since (avant) + fetch_before (passé)
    binance.py           Binance WS (snapshot REST + diffs par séquence ; FUTURES @trade, filtre non-MARKET)
    binance_rest.py      Binance REST (depth snapshot + aggTrades) ; fetch_since/before + fetch_range (SEEK temps)
    bybit.py             Bybit WS v5 (orderbook snapshot/delta par séquence `u` + publicTrade ; ping applicatif)
    bybit_rest.py        Bybit REST recent-trade (RÉCENT seulement : pas d'historique profond → forward only)
    okx.py               OKX WS v5 (books snapshot/update par séquence seqId/prevSeqId + trades ; symboles BTC-USDT-SWAP)
    okx_rest.py          OKX REST history-trades (paginable par `after`=tradeId ; UA navigateur requis) ; fetch_since + fetch_before
    coinbase.py          Coinbase WS Exchange (matches + level2_batch ; SPOT only ; `side`=maker → INVERSÉ ; pas de séquence)
    coinbase_rest.py     Coinbase REST /trades (paginable par `after`=tradeId ; UA requis) ; fetch_since + fetch_before
    kucoin.py            KuCoin WS Spot+Futures (token bullet-public ; level2Depth50 remplacement total ; ts ns ; futures ×multiplier)
    kucoin_rest.py       KuCoin REST recent-trade (RÉCENT seulement, comme Bybit → forward only) ; fetch_since + fetch_before
gui/                     ← dashboard natif (principal)
  service.py             registry FEEDS : tous les flux (exchange × marché) captés en continu
  logsetup.py            log de débogage : `logs/crypto.log` écrasé à chaque lancement (+ console)
  history.py             heatmap en mémoire (échantillonnage du carnet), une par flux, plafonné ~2 h
  backfill.py            pré-charge le préfixe de la chandelle OUVERTE + archivage live (présent seulement)
  collector.py           collecteur d'historique : UN thread par EXCHANGE (parallélisme réseau).
                         Phase A = comble TOUS les trous (>30 s) du présent au PLANCHER Bitget ;
                         phase B = remonte le passé (non-Bitget bornés au plancher). Cooldown anti-429 ;
                         trou confirmé vide = plus retenté ; flux affiché prioritaire
  book_recorder.py       enregistre les snapshots de carnet (1 Hz, thread de fond) → books.db
  book_reader.py         lecture ASYNCHRONE + BORNÉE de books.db (heatmap historique, hors thread Qt)
  rollup_maintainer.py   thread de fond : draine le rollup du footprint en continu (rollup_step) +
                         applique la RÉTENTION (purge trades >7 j déjà agrégés + snapshots carnet)
  currency.py            taux USD→CAD (affichage $US/$CA)
  candles.py             reconstruction bougies + footprint depuis les trades (build_candles)
                         ou depuis l'agrégat du ROLLUP (build_candles_agg, zoom arrière)
  footprint_reader.py    lecture ASYNCHRONE du footprint depuis le ROLLUP (aggregate_footprint_rollup),
                         hors thread Qt -> zoom arrière sans gel, coût borné par l'affichage
  footprint_view.py      FootprintItem + FootprintLayer (couche footprint en avant-plan ; tick stable, V/D)
  orderflow_view.py      graphe UNIQUE : heatmap (grille temps uniforme) + bid/ask + footprint + DOM ;
                         scatters limités à la dernière heure ; résolution FIXE (auto-coarsening retiré)
  controls.py            menu « Affichage » : popups de config par couche (visible/opacité/ordre/options)
  app.py                 fenêtre PySide6 (barre : résolution, date, exchange/marché, Affichage, Live, paire)
run_gui.py               point d'entrée natif
docs/
  bitget.md              fiche Bitget (orderbook/orderflow/connexion/normalisation/pièges)
  binance.md             fiche Binance (idem)
  bybit.md               fiche Bybit (idem ; LIMITE : pas d'historique REST profond)
  okx.md                 fiche OKX (idem ; symboles instId, intégrité seqId)
  coinbase.md            fiche Coinbase (SPOT only ; `side`=maker inversé ; liquidité USDT faible)
  conception.md          DOC TRANSVERSAL : choix de conception, embûches & solutions, adaptations
  hybride.md             CONCEPTION de la vue hybride (recalage prix vers Bitget, plan par phases)
logs/crypto.log          log de débogage (écrasé à chaque lancement)
data/                    bases SQLite (WAL ; supprimables app fermée → recréées ; rétention 7 j)
  trades.db              archive des trades + rollup pré-agrégé du footprint
  books.db               archive des snapshots de carnet (heatmap historique)
tests/                   suite pytest (python -m pytest ; parité/normalisation, archive, collecteur…)
```

## Multi-exchange — comment ça marche

- **Un connecteur par exchange** implémentant `BaseConnector`. Chaque connecteur
  gère ses particularités en interne et ne pousse que des données **normalisées**.
- **Tous les flux captés en continu** : chaque (exchange × marché) est un flux
  distinct dans le hub (clés `bitget`, `bitget_spot`, `binance`, `binance_spot`, …).
  Changer d'exchange/marché dans l'UI ne fait que **changer la clé lue** — aucun
  connecteur n'est arrêté, l'autre flux continue d'être collecté/archivé/affichable.
- **Ajouter un exchange** = écrire `connectors/<ex>.py` (+ `<ex>_rest.py`) + ajouter
  ses flux dans `FEEDS` (gui/service.py) + une fiche `docs/<ex>.md`.

## Schéma unifié (format Bitget)

| Champ        | Convention                                            |
|--------------|-------------------------------------------------------|
| Symbole      | `BTCUSDT`, `ETHUSDT`                                   |
| Prix / size  | floats ; size = quantité en actif de base (BTC, ETH)  |
| Timestamp    | epoch **ms UTC** (horloge de l'exchange)              |
| Side (trade) | `buy` / `sell` = côté de l'agresseur                   |

## Initialisation de l'orderbook (diffère par exchange)

| Exchange | Snapshot initial | Updates | Intégrité            |
|----------|------------------|---------|----------------------|
| Bitget   | **WebSocket**    | WS      | checksum CRC32       |
| Binance  | **REST**         | WS      | numéros de séquence (`U`/`u`/`pu`) |
| Bybit    | **WebSocket**    | WS      | numéro de séquence `u` |
| OKX      | **WebSocket**    | WS      | séquence `seqId`/`prevSeqId` (checksum dispo, non utilisé) |
| Coinbase | **WebSocket**    | WS      | **aucune** (level2_batch sans séquence) → resync = snapshot à la reco |

En cas de checksum/séquence invalide, le connecteur **resynchronise** (nouveau
snapshot). Détails par exchange : `docs/bitget.md`, `docs/binance.md`,
`docs/bybit.md`, `docs/okx.md`, `docs/coinbase.md`.

## Orderflow historique

**Trades** — archivés dans `data/trades.db` (SQLite, mode **WAL**, dédup par
`PRIMARY KEY(market, symbol, trade_id)`), remplis par **deux sources** :
- le **GUI** archive le live en continu et pré-charge le **préfixe de la chandelle
  ouverte** (présent seulement) ;
- le **collecteur** (`gui/collector.py`, **un thread par EXCHANGE**, lancé avec le GUI ;
  parallélisme réseau → un exchange lent/rate-limité n'affame plus les autres). Stratégie
  **ancrée sur Bitget** (cf. [docs/conception.md](docs/conception.md)) :
  - **Bitget = plancher commun** : il remonte le passé librement ; les autres exchanges
    **ne descendent jamais sous** le trade Bitget le plus ancien (on ignore les données
    plus anciennes tant que Bitget ne les a pas couvertes) ;
  - **(A) trous d'abord** (seuil **30 s**, du présent au plancher) **puis (B) passé
    profond**. Un trou confirmé **vide** par REST n'est plus retenté ; sur erreur/429 le
    flux passe en **cooldown** ;
  - **Binance** : seek par temps (`fetch_range`/startTime) ; **Bitget/OKX/Coinbase** :
    `fetch_before` amorcé au **bord du trou** (id WS == id REST) ; **Bybit** : recent-trade
    seulement → trous récents uniquement (forward only).
  On n'insère que les trades dans un trou (`INSERT OR IGNORE`). Flux **affiché**
  prioritaire (budget de comblage élargi). Couverture : Bitget ~90 j, Binance jusqu'au
  listing, OKX/Coinbase selon leur REST (bornés au plancher Bitget), Bybit récent.

Anti double-comptage live/REST par intervalles de couverture live. La navigation
profonde **lit** `trades.db` (plus de pont `ensure_range`).

**Rollup + rétention** — un **rollup pré-agrégé du footprint** (base 60 s × tick
d'instrument × sens) est maintenu **incrémentalement** par un curseur sur le `rowid`
de `trades` (`gui/rollup_maintainer.py` → `archive.rollup_step`) : tout trade inséré
(live, gap-fill, hors ordre) est agrégé **une seule fois**. Le zoom arrière lit ce
rollup (cf. plus bas). La **rétention** purge les trades bruts de plus de **7 jours**
*et déjà agrégés* (le footprint historique survit dans le rollup ; les scatters ne
couvrent de toute façon que la dernière heure) + les vieux snapshots de carnet → les
bases ne grossissent plus sans fin.

**Carnet / heatmap** — aucun historique REST possible → le carnet est **persisté en
live** (snapshots à 1 Hz, `gui/book_recorder.py`) dans une base séparée
`data/books.db`. L'historique du carnet ne grandit donc que **vers l'avant**, pendant
les sessions où l'app tourne. La heatmap utilise l'historique **en mémoire**
(`gui/history.py`, ~2 h récentes) **complété par le disque** (`books.db`) au-delà :
la lecture disque est **bornée** (~1000 colonnes réparties sur la fenêtre, quel que
soit le volume) et **asynchrone** (`gui/book_reader.py`, hors thread Qt) → la
navigation profonde ne gèle jamais, même avec une grosse base / plusieurs exchanges.

## Performance du zoom arrière (mise à l'échelle)

Reconstruire le footprint depuis les trades BRUTS coûte O(trades) : sur une grande
plage ça charge des centaines de milliers de trades **à chaque image** (~300 ms/frame,
gel + GIL saturé). La parade, en deux régimes selon la largeur de fenêtre
(`AGG_SPAN_S`, 30 min) :
- **zoom serré** : chemin brut (réactif pour le footprint live) ;
- **zoom arrière** : footprint lu depuis le **ROLLUP pré-agrégé** (re-bucketisé en
  temps + prix par `archive.aggregate_footprint_rollup`), **hors thread Qt**
  (`footprint_reader.py`) + **debounce** du recalcul. Le coût devient **borné par
  l'affichage** (plus par le nombre de trades) et ne dérive plus quand l'historique
  grandit. Validé : footprint dérivé du rollup **identique** au brut ; refresh côté Qt
  ~3-5 ms/frame. Seul le flux **affiché** est rendu → l'ajout d'exchanges n'alourdit
  pas le rendu. Les **scatters** ne sont tracés que sur la **dernière heure**.

> Une **revue critique** complète (forces, dettes, bugs, plan d'action) est dans
> [`REVUE_CRITIQUE.md`](REVUE_CRITIQUE.md).

## Prochaines étapes

- **Rollup multi-résolution** (optionnel) : pré-agréger aussi à des ticks de prix
  coarsés par résolution → zoom arrière **très large** (24 h+) en ~ms (le rollup
  actuel, mono-résolution 60 s, est déjà borné mais ~100 ms sur de grandes plages).
- **PHASE B (passé profond) : persister le curseur** — il repart de zéro à chaque
  session (cf. [docs/conception.md](docs/conception.md), §dette).
- **Rollup multi-résolution** (perf zoom arrière très large) ; suivi **incrémental** des
  trous (si `find_holes` redevient coûteux après un gros import).
- (c) Connecteurs : **bybit, OKX, coinbase, kucoin** (SPOT+Futures sauf coinbase) : faits.
  **kraken : rejeté** (cf. conception.md §7). Autres candidats éventuels au même format.
- (d) **Vue HYBRIDE** (aboutissement) : orderflow recalé sur l'axe de prix Bitget,
  hybride-Futures et hybride-Spot séparés, par phases — (1) footprint hybride,
  (2) heatmap/DOM, (3) **détection de DISLOCATION** (ex-« arbitrage » ; filtre de taille).
  **Coinbase exclu** (pas de futures,
  USDT peu liquide). Conception : **[`docs/hybride.md`](docs/hybride.md)**.
