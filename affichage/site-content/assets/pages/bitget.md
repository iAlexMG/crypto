---
id: bitget
type: exchange
nom: Bitget
sigle: BG
accent: "#00d3a7"
statut: reference          # reference | actif | actif-exclu | rejete
tagline: "L'exchange de référence — l'étalon vers lequel tout est normalisé"
marches: [Spot, USDT-Futures]
stats:
  - { val: "WS 100 %", label: "snapshot + updates + checksum" }
  - { val: "CRC32", label: "intégrité du carnet (25 niv.)" }
  - { val: "~90 j", label: "historique REST trades" }
book_init:
  snapshot: "WebSocket (action=snapshot) — carnet complet poussé en direct"
  updates: "WebSocket incrémental (action=update ; taille 0 = suppression)"
  integrite: "Checksum CRC32 sur les 25 meilleurs niveaux/côté → resync si invalide"
schema_svg: svg/bitget-carnet.svg
visuel: img/bitget_view.png
---

## Rôle

Bitget est l'exchange de **référence** : son format (symbole BTCUSDT, prix/size en
actif de base, timestamp ms UTC, side = agresseur) **est** le schéma cible du projet.
C'est aussi le **plancher commun** du collecteur d'historique : les autres exchanges
ne descendent jamais sous le trade Bitget le plus ancien.

## Résultats

- Carnet maintenu plein à 300 niveaux (150+150) en permanence sur les deux marchés.
- Historique trades ~90 jours via REST Fills-History (pagination idLessThan).
- Heartbeat texte `"ping"`/`"pong"` applicatif + watchdog (reco si trades figés 120 s).
- Couverture continue des deux marchés (archivage live des deux flux simultanément).

## Embûches & solutions

### Casse du `side` différente WS vs REST
- **Symptôme** — `buy`/`sell` minuscule en WebSocket, `Buy`/`Sell` majuscule en REST.
- **Cause** — Conventions distinctes des deux API Bitget pour un même champ.
- **Solution** — Normalisation systématique en `lower()` avant de pousser dans le hub.

### Snapshot du canal trade à ignorer
- **Symptôme** — Le premier push du canal trade contient des trades antérieurs au lancement.
- **Cause** — Bitget envoie un snapshot d'amorçage sur le canal trade.
- **Solution** — On ignore `action=snapshot` sur le canal trade ; l'historique vient du REST.

### Pas d'orderbook historique en REST
- **Symptôme** — Impossible de reconstituer une heatmap de liquidité passée.
- **Cause** — Limite dure de l'API : aucun endpoint d'orderbook historique.
- **Solution** — Persistance **live** des snapshots de carnet à 1 Hz (books.db) → la heatmap historique ne grandit que vers l'avant.
