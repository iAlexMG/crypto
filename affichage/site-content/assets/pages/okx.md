---
id: okx
type: exchange
nom: OKX
sigle: OK
accent: "#7b61ff"
statut: actif
tagline: "Chaînage de séquence propre — deux pièges coûteux"
marches: [Spot, "Swap (USDT perp)"]
stats:
  - { val: "WS 100 %", label: "snapshot puis update" }
  - { val: "seqId / prevSeqId", label: "chaînage de séquence (checksum dispo, non utilisé)" }
  - { val: "paginable", label: "history-trades (after=tradeId)" }
book_init:
  snapshot: "WebSocket action=snapshot (books, 400 niveaux) → reset"
  updates: "WebSocket action=update (taille 0 = suppression)"
  integrite: "Chaînage seqId/prevSeqId (prevSeqId == dernier seqId), sinon resync"
schema_svg: svg/okx-carnet.svg
visuel: img/okx_view.png
---

## Rôle

Intégré de bout en bout, support complet du collecteur (REST paginable). Deux pièges
sérieux résolus : le 403 REST et la taille en contrats.

## Résultats

- Symboles instId (`BTC-USDT-SWAP` / `BTC-USDT`) re-normalisés vers BTCUSDT pour le hub.
- Historique trades paginable (`after=tradeId`) → support complet du collecteur (≠ Bybit).
- Comblage de trou amorcé au bord (id WS == id REST chez OKX → sûr).

## Embûches & solutions

### REST 403 (et ce n'était PAS une géo-restriction)
- **Symptôme** — Toutes les requêtes REST renvoyaient 403 ; on a d'abord cru à un blocage géographique.
- **Cause** — OKX bloque le User-Agent par défaut d'urllib.
- **Solution** — User-Agent navigateur explicite (`Mozilla/5.0`) dans `okx_rest.py` → 200.

### Taille en contrats → volume ×100
- **Symptôme** — Le volume OKX était 100× (BTC) / 10× (ETH) trop gros → footprint hybride dominé par OKX + points scatter géants.
- **Cause** — `sz` d'un SWAP = nombre de **contrats**, pas l'actif de base.
- **Solution** — ×ctVal (BTC-USDT-SWAP 0,01 ; ETH-USDT-SWAP 0,1), appliqué aux trades ET au carnet (`CONTRACT_VAL`).
