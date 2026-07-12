# Méthode B — ticks crypto via Quantower → SQLite (multi-exchange)

Stratégie Quantower (`Crypto Tick Extractor`) qui télécharge les ticks d'un symbole crypto
via une connexion **déjà authentifiée dans Quantower** — Binance, Bybit, OKX, … : c'est la
connexion du symbole choisi qui fixe l'exchange — et les écrit au **schéma exact** de la
méthode A (`binance_history.py`, `bybit_history.py`) → la chaîne Python aval (`candles.py`, …)
tourne sans modification. Adaptée de l'extracteur NQ/Rithmic archivé
(`Portfolio/_archive/Quantower/extractor`), dont les mesures de Phase 0 ont fixé
l'architecture : le BusinessLayer ne se connecte pas hors du process Quantower, donc le code
tourne **dans** Quantower.

L'exchange est déduit de `Symbol.Connection.VendorName` (vérifié par réflexion v1.146.14 ;
non renommable par l'utilisateur, contrairement au nom de connexion) et nomme la base :

| Connexion | Base produite (défaut) | Voie A homologue |
|---|---|---|
| Binance | `<sym>-<um\|spot>-quantower.db` (schéma d'origine, rétro-compat) | `<sym>-<um\|spot>-api.db` |
| Bybit (et toute autre venue) | `<sym>-bybit-quantower.db` | `<sym>-bybit-api.db` |

## Schéma produit (identique à la méthode A)

```sql
trades(trade_id INTEGER PRIMARY KEY,  -- rowid, insertion append = ordre chronologique
       ts INTEGER,                    -- ms UTC
       price REAL, size REAL,
       side TEXT)                     -- 'buy'/'sell' = côté agresseur (AggressorFlag)
_meta(k,v)        -- symbol, market, exchange, connection, tick_size, source=quantower-<exchange>
_ingested(name,rows,at)   -- 'day/YYYY-MM-DD' pour les jours complets déjà collectés
idx_trades_ts ON trades(ts)
```

Vérifié par réflexion sur la DLL v1.146.14 : `HistoryItemLast` n'expose **pas** de `TradeId`
(même verrou que Rithmic) → `trade_id` = rowid auto, et l'incrémental repose sur le marquage
par jour. Les ticks sans côté agresseur sont exclus (jamais de `side` vide) et **comptés**
(voir mesures).

## Incrémental & idempotent

- Les **jours complets passés** sont marqués dans `_ingested` et jamais re-téléchargés.
- Le **jour courant** (partiel) est purgé puis ré-inséré à chaque run → relancer est sûr.
- Backfill depuis **« Début historique »** (défaut `2026-06-01`, cible projet juin–juillet
  2026). Cette date **prime sur la reprise** : la relever fait sauter les jours plus anciens
  (trou assumé dans la base) ; la baisser ne re-télécharge PAS les jours déjà marqués.
- **« Fin historique »** (optionnel, vide = aujourd'hui) : borne la fenêtre — sans borne, la
  collecte va de Début historique à AUJOURD'HUI (même fenêtre que la voie A), c'est le
  comportement voulu pour la validation croisée.
- **Stop = arrêt propre** : la passe s'interrompt à la **fin du jour en cours** (un jour dont
  le téléchargement est lancé va au bout ; le log le dit dès le clic). ⚠️ Après Stop, la
  plateforme peut continuer d'afficher des dates qui avancent : c'est le téléchargeur
  d'historique de **Quantower** qui vide sa file, pas la stratégie.
- **Garde-fous** (ajoutés après le premier essai Bybit, nuit du 2026-07-12) :
  - connexion du symbole **non active** → refus immédiat avec la cause (avant : passe
    « vide » silencieuse, 0 tick servi, base créée pour rien) ;
  - **identité de base** : écrire dans une base non vide construite pour une AUTRE venue est
    refusé (ex. ticks Bybit dans la base Binance validée) — comparaison par slug d'exchange,
    tolérante aux métadonnées des anciennes versions.
- Paramètre **« Collecte auto toutes les N heures »** (défaut 6, `0` = one-shot) : recollecte
  seule à cet intervalle tant que la stratégie tourne. ⚠️ La collecte n'a lieu **que quand
  Quantower est ouvert** avec la connexion du symbole active — limite structurelle de la
  méthode, à mettre en regard des archives de la méthode A téléchargeables à toute heure.

## Déployer & lancer

```powershell
# -ExecutionPolicy Bypass : la politique du système bloque les .ps1 ; le flag ne vaut
# que pour cette commande, sans changer la configuration.
powershell -ExecutionPolicy Bypass -File historique\quantower_extractor\deploy.ps1
```

⚠️ **Quantower ne charge les DLL de stratégies qu'au démarrage** : après un déploiement,
redémarrer la plateforme, sinon c'est l'ancienne version qui tourne (vérifiable au nom —
l'ancienne s'appelait « Crypto Tick Extractor (Binance) », l'actuelle « Crypto Tick
Extractor » tout court).

Puis dans Quantower (connexion voulue active — Binance, Bybit, …) : panneau **Strategies** →
`Crypto Tick Extractor` → paramètre **Symbole = BTCUSDT** *depuis la bonne connexion*
(futures perp si la connexion les expose, sinon spot) → **Start**. Suivre l'onglet **Logs**
(la première ligne « Base : … » confirme le fichier et la connexion utilisés).
Résultat par défaut : `historique\data\BTCUSDT-um-quantower.db` (Binance) ou
`historique\data\BTCUSDT-bybit-quantower.db` (Bybit, etc.) — la paire à comparer se lit
dans les noms, et aucune voie ne peut écraser l'autre.

## Mesures — premier run réel (2026-07-11, janvier 2026)

Le premier run a tranché ce que la doc ne disait pas (chaque passe complète relogue les
lignes `MESURE profondeur / agresseur / unités` pour surveiller une dérive) :

| Question | Réponse mesurée |
|---|---|
| Profondeur d'historique tick | **profonde** : janvier 2026 servi intégralement dès la demande (≥ 6 mois ; borne réelle non sondée) — Rithmic plafonnait à ~2 semaines |
| Nature des ticks servis | **exactement les aggTrades officiels** : journée témoin 2026-01-02, 1 621 563 trades et 176 662,45 BTC des deux côtés (A et B), répartition acheteur comprise |
| Unité de `size` | **actif de base (BTC)** — volumes identiques à la méthode A |
| Débit / volumétrie | **~40 s/jour** de collecte, ~41 M de ticks et ~1,6 Go par mois |

## Validation croisée avec la méthode A

Identité parfaite constatée sur la journée témoin (voir tableau). Pour rejouer la
comparaison sur une autre fenêtre, reconstruire les chandelles des deux côtés :

```powershell
python ..\candles.py --db ..\data\BTCUSDT-um-quantower.db --chart candles\BTCUSDT-um-quantower.html --open
python ..\candles.py --db ..\data\BTCUSDT-um-api.db --chart candles\BTCUSDT-um-api.html --open
```
