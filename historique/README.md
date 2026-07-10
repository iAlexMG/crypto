# Extracteur d'historique Binance — autonome

Un **seul fichier** (`binance_history.py`), **stdlib pure** (aucun `pip install`), **zéro
import du projet Crypto**. Conçu pour être **copié tel quel** dans un autre sous-projet.

Il récupère l'historique **le plus long possible** des **trades tick-par-tick** (aggTrades)
d'un symbole — par défaut **BTCUSDT, Futures perp (USDⓈ-M)** — normalisés comme dans le
projet : `ts` (ms UTC), `price`, `size` (en BTC), `side` (`buy`/`sell` = côté agresseur),
`trade_id`.

## Pourquoi des dumps et pas le REST ?

Le collecteur du projet pagine les REST (`fetch_before`/`fetch_range`). Pour aller jusqu'au
listing (BTCUSDT perp ≈ **fin 2019**) cela ferait des **millions** de requêtes. Binance
publie les **mêmes** trades en archives sur **data.binance.vision** : ~100 fichiers au lieu de
millions d'appels. Même donnée, **même normalisation** (`is_buyer_maker=true → side="sell"`).
`--rest-tail` complète le dernier jour via le REST (le pattern `fetch_since` du projet).

## Utilisation

```bash
# Voir le plan sans rien télécharger (recommandé en premier)
python binance_history.py --dry-run
# -> ~107 fichiers, 2019-12-31 → aujourd'hui

# Tout l'historique BTCUSDT perp -> SQLite (long + dizaines de Go ; reprenable)
python binance_history.py

# Une fenêtre précise -> CSV gzip (1 fichier par période)
python binance_history.py --start 2024-01 --end 2024-03 --format csv --out ./csv_out

# Spot au lieu des futures, autre symbole
python binance_history.py --market spot --symbol ETHUSDT

# Compléter jusqu'à maintenant après les dumps (sqlite, marchés um/spot)
python binance_history.py --rest-tail
```

## Cible courante : janvier → juillet 2026 (plage modifiable)

La plage est **librement modifiable** via `--start`/`--end`. Cible actuelle du projet (reset
de `F:\data` le 2026-07-01) : **2026-01 → 2026-07** (~6 mois de ticks, plusieurs centaines de
millions de trades attendus). Sous PyCharm, ce workflow est piloté par les 3 run configs :

| Config PyCharm | Commande équivalente | Rôle |
|---|---|---|
| `1 - dry-run` | `binance_history.py --start 2026-01 --end 2026-07 --dry-run` | voir le plan (fichiers, tailles) sans télécharger |
| `2 - Extraction` | `binance_history.py --start 2026-01 --end 2026-07 --out F:\data\BTCUSDT-um.db --cache F:\data\_dumps` | extraction réelle → SQLite cumulative |
| `3 - Chandelles` | `candles.py --db F:\data\BTCUSDT-um.db --tf 1H --chart candles\BTCUSDT-1H.html --open` | contrôle visuel post-extraction |

```bash
# 1) Extraire les trades tick-par-tick dans la base cumulative du projet
python binance_history.py --start 2026-01 --end 2026-07 ^
  --out F:\data\BTCUSDT-um.db --cache F:\data\_dumps

# 2) Reconstruire les chandelles 1H et les afficher sur un graphique interactif
python candles.py --db F:\data\BTCUSDT-um.db --tf 1H ^
  --chart candles\BTCUSDT-1H.html --open
```

> La base `BTCUSDT-um.db` est **cumulative** (`INSERT OR IGNORE` dédoublonne) : pour ajouter de
> l'historique, relancez avec une autre plage `--start`/`--end`, ça s'ajoute sans doublon. Le plan
> bascule automatiquement sur les dumps **quotidiens** pour le mois courant / partiel.

## Suite du pipeline (côté cours, hors de ce sous-projet)

Ce sous-projet s'arrête à la **base de ticks validée**. Tout ce qui est en aval vit dans
`backtests/` (environnement conda `backtesting`, pandas) et doit être **régénéré après chaque
extension de la base** — et intégralement après un reset de `F:\data` :

```bash
python history_extractor\candles.py                # 0) contrôle visuel de la base (ce dossier)
python backtests\normalize.py                      # 1) source de vérité OHLCV -> F:\data\ohlcv\BTCUSDT-um\{1H,4H,D}.csv
python backtests\volume_profile.py --start 2026-01-01 --end 2026-07-01 --features --zone session:13:30-20:00
                                                   # 2) features footprint/VP (si stratégies à features)
python backtests\lean\sync_spec.py <strategie> ... # 3) rafraîchit le CSV monté par LEAN + config
python backtests\check_causality.py                # 4) garde-fou parité (prefix-stability)
```

L'ordre compte : `normalize.py` lit la base de ticks, `volume_profile.py` aussi (ajuster
`--start/--end` à la plage réellement extraite), `sync_spec.py` joint les deux dans le CSV
que consomme le conteneur LEAN.

## Validation visuelle des chandelles (`candles.py --chart`)

`candles.py` reconstruit les chandelles **OHLCV (D / 4H / 1H)** depuis les trades, en une
passe streaming sur l'ordre des `trade_id` (= ordre chronologique, sans tri ni index `ts`).

`--chart FICHIER.html` écrit un **graphique chandelier interactif AUTONOME** — canvas pur,
**zéro dépendance / zéro CDN**, ouvrable hors-ligne (cohérent avec la philosophie stdlib) :

- chandelles vertes/rouges + barres de **volume**, axes prix (droite) et temps (UTC) ;
- **crosshair + infobulle** (OHLC, volume, % volume acheteur, nombre de trades) ;
- **molette = zoom**, **glisser = défiler**, **double-clic = réinitialiser** ;
- **sélecteur de pas de temps** (boutons D / 4H / 1H) si plusieurs `--tf` sont fournis.

```bash
python candles.py --db F:\data\BTCUSDT-um.db --tf 1H --chart btc.html --open
python candles.py --db F:\data\BTCUSDT-um.db --csv ./candles   # + séries CSV complètes
```

## Options

| Option | Défaut | Rôle |
|--------|--------|------|
| `--symbol` | `BTCUSDT` | symbole |
| `--market` | `um` | `um` (USDⓈ-M futures), `cm` (COIN-M), `spot` |
| `--start` / `--end` | début dispo / mois courant | `YYYY`, `YYYY-MM` ou `YYYY-MM-DD` |
| `--format` | `sqlite` | `sqlite` ou `csv` (gzip) |
| `--out` | auto | fichier `.db` (sqlite) ou dossier (csv) |
| `--cache` | `./_binance_dumps` | cache des zips téléchargés |
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
c = sqlite3.connect("BTCUSDT-um-aggtrades.db")
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
  après ingestion (sauf `--keep-zips`), `--prefetch` borne le nombre en cache. La base SQLite 
  complète se chiffre en **dizaines de Go** (des centaines de millions de trades).
```
