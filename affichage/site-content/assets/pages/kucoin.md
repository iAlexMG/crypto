---
id: kucoin
type: exchange
nom: KuCoin
sigle: KC
accent: "#23af91"
statut: actif
tagline: "Token WS à bootstrapper, nanosecondes et contrats"
marches: [Spot, "Futures (XBTUSDTM)"]
stats:
  - { val: "level2Depth50", label: "carnet par remplacement total" }
  - { val: "ts en ns", label: "timestamps en nanosecondes" }
  - { val: "×multiplier", label: "futures en contrats (XBT)" }
book_init:
  snapshot: "WebSocket level2Depth50 — chaque push = top-50 complet"
  updates: "Remplacement total à chaque push (~100 ms), pas d'incrémental"
  integrite: "Aucune séquence nécessaire : un push perdu est corrigé au suivant"
schema_svg: svg/kucoin-carnet.svg
visuel: img/kucoin_view.png
---

## Rôle

6e exchange, Spot + Futures. Futures = perps USDT → éligible à l'hybride-Futures.
Plusieurs particularités de format à apprivoiser.

## Résultats

- Token WS bootstrappé via POST `/bullet-public` (public) puis connexion avec token+connectId.
- Ping applicatif `{"type":"ping"}` avant l'échéance pingInterval (~18 s).
- `side` = taker = agresseur (pas d'inversion, contrairement à Coinbase).

## Embûches & solutions

### Pas d'URL WebSocket fixe
- **Symptôme** — Impossible de se connecter à une URL WS connue d'avance.
- **Cause** — KuCoin distribue un token + endpoint par POST `/bullet-public`.
- **Solution** — Bootstrap du token (via `run_in_executor` pour ne pas bloquer la boucle asyncio partagée).

### Tailles futures en contrats
- **Symptôme** — Les tailles futures étaient ~1000× trop grandes.
- **Cause** — WS et REST futures donnent la taille en nombre de contrats.
- **Solution** — ×multiplier (XBTUSDTM 0,001 ; ETHUSDTM 0,01). Spot = 1. (Même piège qu'OKX.)

### Timestamps en nanosecondes
- **Symptôme** — Les horodatages étaient 10⁶ fois trop grands → trades hors écran.
- **Cause** — KuCoin émet les ts en nanosecondes (WS et REST).
- **Solution** — Conversion `to_ms()` (÷ 1e6) systématique.

### Bitcoin s'appelle XBT en futures
- **Symptôme** — Le symbole BTCUSDT ne matchait pas les futures KuCoin.
- **Cause** — KuCoin Futures utilise `XBTUSDTM` (BTC → XBT, suffixe M).
- **Solution** — Mapping BTCUSDT ↔ XBTUSDTM dans `market_symbol()`.
