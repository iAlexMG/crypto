---
id: arbitrage
type: feature
titre: "Arbitrage — détection en direct"
eyebrow: "Phase 3"
accent: "#ffcb45"
formule: "net_bps = edge/mid × 10⁴ − fee_taker(X) − fee_taker(Y)"
visuel: img/arbitrage_view.png
features:
  - "Moteur pur find_arbs(books, fees, threshold) — testé headless (sens/edge/net exacts)."
  - "Meilleure opportunité affichée dans la barre d'état ; toutes journalisées dans arb.db (purge 7 j)."
  - "Marqueurs étoiles dorées au mid de chaque opportunité (dernière heure + fenêtre visible)."
  - "Table de frais éditable (bouton « Arbitrage ⚙ ») : frais taker par exchange×marché + seuil."
fees:
  - { ex: "Bitget",  fut: "0,06 % (6 bps)",   spot: "0,10 %" }
  - { ex: "Binance", fut: "0,05 % (5 bps)",   spot: "0,10 %" }
  - { ex: "Bybit",   fut: "0,055 % (5,5 bps)", spot: "0,10 %" }
  - { ex: "OKX",     fut: "0,05 % (5 bps)",   spot: "0,10 %" }
  - { ex: "KuCoin",  fut: "0,06 % (6 bps)",   spot: "0,10 %" }
---

## Intro

Sur les carnets live du périmètre, on teste toutes les paires ordonnées (vendre au best
bid de X, acheter au best ask de Y) sur les prix **bruts** — un arbitrage est un écart
réel, pas recalé. Le seuil est en bps **net** après frais taker.

## Formule

`net_bps = edge/mid × 10⁴ − fee_taker(X) − fee_taker(Y)`

## Ce qui est livré

Voir le bloc `features` du frontmatter.

## Frais taker par défaut (éditables)

Voir le bloc `fees` (Exchange / Futures / Spot).

> Frais par défaut = palier STANDARD VIP 0, sans réduction token (sources publiques 2026),
> tous éditables. Le seuil par défaut est 1 bps net.
