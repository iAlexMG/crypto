# Historique — les trades tick-par-tick, par deux voies, exchange par exchange

Récupérer l'historique des **trades tick-par-tick** d'un symbole — par défaut **BTCUSDT** —
normalisés au schéma commun du projet : `ts` (ms UTC), `price`, `size` (en BTC), `side`
(`buy`/`sell` = côté agresseur), `trade_id`. **Cible courante : juin–juillet 2026** (du
2026-06-01 à aujourd'hui, extensible), les données vivent dans `data\` (gitignoré), dans
le dépôt. **Binance** (validé, comparatif plus bas) est le modèle de référence ; **Bybit**
suit le même montage (section dédiée plus bas).

Deux méthodes produisent la **même base SQLite**, comparées plus bas :

- **Méthode A — archives officielles** (`binance_history.py`, `bybit_history.py`) :
  data.binance.vision (+ complément REST) / public.bybit.com. Un **seul fichier** par
  exchange, **stdlib pure** (aucun `pip install`), **zéro import du projet Crypto**,
  copiable tel quel ailleurs.
- **Méthode B — Quantower** ([`quantower_extractor/`](quantower_extractor/README.md)) :
  stratégie C# multi-exchange qui tourne dans Quantower et télécharge les ticks via le
  BusinessLayer — la connexion du symbole choisi (Binance, Bybit, …) fixe l'exchange et le
  nom de la base.

## Méthode A — pourquoi des dumps et pas le REST ?

Le collecteur du projet pagine les REST (`fetch_before`/`fetch_range`). Pour aller jusqu'au
listing (BTCUSDT perp ≈ **fin 2019**) cela ferait des **millions** de requêtes. Binance
publie les **mêmes** trades en archives sur **data.binance.vision** : quelques fichiers au lieu
de millions d'appels. Même donnée, **même normalisation** (`is_buyer_maker=true → side="sell"`).
`--rest-tail` complète le dernier jour via le REST (le pattern `fetch_since` du projet).

## Utilisation (méthode A)

```bash
# Voir le plan sans rien télécharger (recommandé en premier)
python binance_history.py --start 2026-06 --dry-run
# -> 11 fichiers (le mensuel de juin + juillet en quotidiens), 2026-06 → aujourd'hui

# La cible du projet : juin–juillet 2026 -> data\BTCUSDT-um-api.db (reprenable)
python binance_history.py --start 2026-06

# Une fenêtre précise -> CSV gzip (1 fichier par période)
python binance_history.py --start 2026-01 --end 2026-03 --format csv --out ./csv_out

# Spot au lieu des futures, autre symbole
python binance_history.py --market spot --symbol ETHUSDT --start 2026-06

# Compléter jusqu'à maintenant après les dumps (sqlite, marchés um/spot)
python binance_history.py --rest-tail
```

## Cible courante : juin–juillet 2026 (plage modifiable)

La plage est **librement modifiable** via `--start`/`--end`. Cible actuelle du projet :
**`--start 2026-06`** (fin par défaut = mois courant) — un périmètre volontairement réduit
(~2 mois, ~4 Go mesurés), extensible ensuite ; les bases janvier→juillet (31 Go) ont été
supprimées le 2026-07-11, trop grosses pour la machine. Les données vivent dans
**`historique\data\`** (gitignoré à la racine du dépôt). Sous PyCharm, le workflow est
piloté par les 3 run configs :

| Config PyCharm | Commande équivalente | Rôle |
|---|---|---|
| `1 - dry-run` | `binance_history.py --start 2026-06 --dry-run` | voir le plan (fichiers, tailles) sans télécharger |
| `2 - Extraction` | `binance_history.py --start 2026-06` | extraction réelle → `data\BTCUSDT-um-api.db` (cache `data\_dumps`) |
| `3 - Chandelles` | `candles.py --chart candles\BTCUSDT-um-api.html --open` | contrôle visuel post-extraction (pas par défaut : 1m 1H D) |

> La base `data\BTCUSDT-um-api.db` est **cumulative** (`INSERT OR IGNORE` dédoublonne) : pour
> ajouter de l'historique, relancez avec une autre plage `--start`/`--end`, ça s'ajoute sans
> doublon. Le plan bascule automatiquement sur les dumps **quotidiens** pour le mois courant /
> partiel.

## Méthode B — les mêmes ticks via Quantower

[`quantower_extractor/`](quantower_extractor/README.md) : une stratégie Quantower
(`Crypto Tick Extractor`, **multi-exchange** : la connexion du symbole choisi fixe
l'exchange et le nom de la base) adaptée de l'extracteur NQ/Rithmic archivé
(`Portfolio/_archive/Quantower/extractor`). Elle tourne **dans** Quantower (les connexions
n'y sont authentifiées que là), télécharge les ticks jour par jour via
`GetTickHistory(HistoryType.Last, …)` et écrit `data\BTCUSDT-um-quantower.db` (Binance) /
`data\BTCUSDT-bybit-quantower.db` (Bybit, …) au **même schéma** — `candles.py` les lit sans
modification. Incrémentale (jours complets marqués) et idempotente (jour courant
purgé/ré-inséré).

```powershell
powershell -ExecutionPolicy Bypass -File quantower_extractor\deploy.ps1   # build + copie dans Settings\Scripts\Strategies
# puis dans Quantower : Strategies -> Crypto Tick Extractor -> Symbole (sa connexion fixe l'exchange) -> Start
```

### Comparatif A / B

Les cellules « mesuré » viennent du **premier run réel** (2026-07-11, janvier 2026 extrait
des deux côtés — détail dans le [README de l'extracteur](quantower_extractor/README.md)) :

| Critère | A — archives data.binance.vision | B — Quantower (channel Binance) |
|---|---|---|
| Granularité | aggTrades officiels (agrégés par prix/côté/ms) | **mesuré : les mêmes aggTrades** — comptes et volumes identiques au trade près (journée témoin 2026-01-02) |
| Profondeur d'historique | jusqu'au listing du contrat (fin 2019) | **mesuré : profonde** — janvier 2026 servi intégralement (≥ 6 mois ; borne réelle non sondée) ; Rithmic plafonnait à ~2 semaines |
| Identifiant de trade | `trade_id` officiel → dédup `INSERT OR IGNORE` | aucun `TradeId` exposé (vérifié, DLL v1.146.14) → rowid + marquage par jour |
| Côté agresseur | `is_buyer_maker` officiel, 100 % renseigné | `AggressorFlag` calculé par Quantower — **mesuré : répartition buy/sell identique à A** sur la journée témoin |
| Intégrité | SHA256 vérifié contre les `.CHECKSUM` publiés | aucune somme de contrôle — mais vérifiable contre A |
| Automatisation | script headless, relançable/planifiable à toute heure | collecte **seulement Quantower ouvert** + connexion active |
| Dépendances | Python stdlib uniquement | Quantower + .NET 10 + connexion Binance configurée |
| Reprise | par fichier d'archive (`_ingested`) | par jour (`_ingested`), jour courant rejoué ; « Début historique » prime |
| Temps d'extraction | borné par le débit réseau (11 fichiers pour juin–juillet) | **mesuré : ~40 s/jour** (~25 min pour juin–juillet), ~1,6 Go/mois |

**Verdict** : les deux voies produisent la **même donnée au trade près**. La méthode A reste
la voie principale (intégrité vérifiable par SHA256, automatisation sans plateforme ouverte,
profondeur garantie jusqu'à 2019) ; la méthode B, validée comme **contre-épreuve**, sert de
source de contrôle indépendante et de complément pratique quand Quantower est déjà ouvert.

### Validation croisée

Premier résultat (journée témoin 2026-01-02) : **1 621 563 trades et 176 662,45 BTC de
volume des deux côtés, répartition acheteur comprise — identité parfaite.** Pour rejouer la
comparaison sur une autre fenêtre, reconstruire les chandelles des deux côtés :

```bash
python candles.py --db data\BTCUSDT-um-api.db --chart candles\BTCUSDT-um-api.html --open
python candles.py --db data\BTCUSDT-um-quantower.db --chart candles\BTCUSDT-um-quantower.html --open
```

## Bybit — le même montage, deux voies (`bybit_history.py`)

Le pendant Bybit du montage Binance, mêmes philosophies (stdlib pure, reprise, schéma
commun) et même paire de bases : `data\BTCUSDT-bybit-api.db` (voie A) ↔
`data\BTCUSDT-bybit-quantower.db` (voie B).

- **Méthode A** : archives publiques quotidiennes de **public.bybit.com** (un csv.gz par
  jour et par symbole ; BTCUSDT dérivés remonte au **2020-03-25**). Le REST public est
  inutilisable pour le passé (recent-trade plafonné, sans pagination) — constat documenté
  sur la page Bybit du pilier affichage.
- **Méthode B** : la même stratégie Quantower (multi-exchange) sur un symbole de la
  connexion Bybit.

Différences structurelles avec Binance, mesurées sur fichier témoin (2026-07-10) :

| Point | Binance | Bybit |
|---|---|---|
| Identifiant de trade | `trade_id` entier officiel → dédup `INSERT OR IGNORE` | `trdMatchID` = UUID → **rowid**, ingestion **transactionnelle par fichier** (interrompu = rollback, jamais de doublon) |
| Ordre chronologique | garanti par le `trade_id` PK | flux vérifié monotone à l'ingestion (repli : tri stable par ts) + **refus de backfill** avant le contenu existant (hypothèse `candles.py` : rowid = chrono) |
| Horodatage | ms entières | secondes epoch à fraction sous-ms → arrondi ms |
| Côté agresseur | `is_buyer_maker` à inverser | `side` Buy/Sell = agresseur direct |
| Intégrité | SHA256 vs `.CHECKSUM` publiés | pas de somme publiée → **CRC32 du gzip** (décompression complète avant ingestion) |
| Granularité | aggTrades (agrégés par prix/côté/ms) | trades bruts |

```bash
# voir le plan sans rien télécharger
python bybit_history.py --start 2026-06 --dry-run

# la cible du projet : juin-juillet 2026 -> data\BTCUSDT-bybit-api.db (reprenable)
python bybit_history.py --start 2026-06

# contrôle visuel post-extraction
python candles.py --db data\BTCUSDT-bybit-api.db --chart candles\BTCUSDT-bybit-api.html --open
```

Mesuré au premier run (juin–juillet 2026) : ~50–60 Mo et ~2–6 M de trades par jour,
ingestion ~150 k trades/s. La validation croisée A/B façon Binance se rejoue avec
`candles.py` sur les deux bases dès que la voie B a collecté la même fenêtre.

## Suite du pipeline (côté cours, hors de ce sous-projet)

Ce sous-projet s'arrête à la **base de ticks validée**. Tout ce qui est en aval vit dans
`backtests/` (environnement conda `backtesting`, pandas) et doit être **régénéré après chaque
extension de la base** :

```bash
python historique\candles.py                       # 0) contrôle visuel de la base (ce dossier)
python backtests\normalize.py                      # 1) source de vérité OHLCV -> ohlcv\BTCUSDT-um\{1H,4H,D}.csv
python backtests\volume_profile.py --start 2026-01-01 --end 2026-07-01 --features --zone session:13:30-20:00
                                                   # 2) features footprint/VP (si stratégies à features)
python backtests\lean\sync_spec.py <strategie> ... # 3) rafraîchit le CSV monté par LEAN + config
python backtests\check_causality.py                # 4) garde-fou parité (prefix-stability)
```

> ⚠️ Les scripts de `backtesting/` référencent encore `F:/data` en dur (base de ticks et
> sorties OHLCV). Depuis le rapatriement, la base de ticks est `historique\data\BTCUSDT-um-api.db` :
> pointer ces constantes dessus (ou les migrer) à la prochaine régénération.

L'ordre compte : `normalize.py` lit la base de ticks, `volume_profile.py` aussi (ajuster
`--start/--end` à la plage réellement extraite), `sync_spec.py` joint les deux dans le CSV
que consomme le conteneur LEAN.

## Validation visuelle des chandelles (`candles.py --chart`)

`candles.py` reconstruit les chandelles **OHLCV à n'importe quel pas** (`--tf` accepte
`<n>s|m|H|D` : `30s`, `1m`, `5m`, `1H`, `D`… ; défaut `1m 1H D` — le 1m est la vue de
travail scalping, 1H/D le contexte) depuis les trades, en une passe streaming sur l'ordre
des `trade_id` (= ordre chronologique, sans tri ni index `ts`) : l'agrégation se fait au
PGCD des pas demandés puis chaque pas est un rollup exact.

`--chart FICHIER.html` écrit un **graphique chandelier interactif AUTONOME** — canvas pur,
**zéro dépendance / zéro CDN**, ouvrable hors-ligne (cohérent avec la philosophie stdlib) :

- chandelles vertes/rouges + barres de **volume**, axes prix (droite) et temps (UTC) ;
- **crosshair + infobulle** (OHLC, volume, % volume acheteur, nombre de trades) ;
- **molette = zoom**, **glisser = défiler**, **double-clic = réinitialiser** ;
- **sélecteur de pas de temps** (un bouton par pas demandé) si plusieurs `--tf` sont fournis.

```bash
python candles.py --chart btc.html --open                # 1m/1H/D depuis data\BTCUSDT-um-api.db
python candles.py --tf 15s 1m 5m --chart scalp.html      # résolutions scalping, pas libres
python candles.py --db data\BTCUSDT-um-quantower.db --csv ./candles  # base méthode B + séries CSV complètes
```

## Options (`binance_history.py`)

| Option | Défaut | Rôle |
|--------|--------|------|
| `--symbol` | `BTCUSDT` | symbole |
| `--market` | `um` | `um` (USDⓈ-M futures), `cm` (COIN-M), `spot` |
| `--start` / `--end` | début dispo / mois courant | `YYYY`, `YYYY-MM` ou `YYYY-MM-DD` |
| `--format` | `sqlite` | `sqlite` ou `csv` (gzip) |
| `--out` | `data\<SYMBOLE>-<marché>-api.db` | fichier `.db` (sqlite) ou dossier (csv) |
| `--cache` | `data\_dumps` | cache des zips téléchargés |
| `--prefetch` | `2` | zips téléchargés en avance (borne le disque) |
| `--keep-zips` | off | garder les zips bruts après ingestion |
| `--no-verify` | off | ne pas vérifier les SHA256 (`.CHECKSUM`) |
| `--newest-first` | off | traiter du plus récent au plus ancien |
| `--rest-tail` | off | combler du dernier dump → maintenant via le REST |
| `--dry-run` | off | afficher le plan, ne rien télécharger |

## Sortie

**SQLite** (défaut) — fichier unique, mode WAL :

```sql
trades(trade_id INTEGER PRIMARY KEY, ts INTEGER, price REAL, size REAL, side TEXT)
-- + index sur ts, table _meta (symbol/market), table _ingested (reprise)
```

`INSERT OR IGNORE` sur `trade_id` → **déduplication** automatique. Exemple de lecture :

```python
import sqlite3
c = sqlite3.connect(r"data\BTCUSDT-um-api.db")
c.execute("SELECT ts, price, size, side FROM trades WHERE ts BETWEEN ? AND ? ORDER BY ts",
          (start_ms, end_ms))
```

**CSV** (`--format csv`) — un `SYMBOL-market-aggTrades-PÉRIODE.csv.gz` par période,
entête `ts,price,size,side,trade_id`.

## Reprise & robustesse

- **Repriseable** : chaque fichier ingéré est marqué (`_ingested` en SQLite, présence du
  `.csv.gz` en CSV). Relancer la commande **reprend** où ça s'était arrêté.
- **`Ctrl-C`** entre deux fichiers : sûr. En plein fichier : le fichier sera re-traité au
  prochain lancement (l'`INSERT OR IGNORE` dédoublonne).
- **Intégrité** : SHA256 vérifié contre le `.CHECKSUM` publié (désactivable).
- **Plan = listing réel** : chaque URL planifiée provient du listing S3 → pas de 404 deviné
  (mensuels pour les mois complets, quotidiens pour le 1er mois partiel et le mois courant ;
  bascule auto en quotidien si un mensuel n'est pas encore publié).
- **Disque** : un mensuel récent BTCUSDT peut peser **~1–2 Go** zippé ; les zips sont supprimés
  après ingestion (sauf `--keep-zips`), `--prefetch` borne le nombre en cache. Ordres de
  grandeur mesurés : **~40 M de trades par mois** (janvier 2026) ; base A janvier→juin 2026 =
  18,7 Go (~3 Go/mois, `trade_id` officiels volumineux), base B ~1,6 Go/mois (rowids
  compacts) — d'où le `data/` gitignoré.
