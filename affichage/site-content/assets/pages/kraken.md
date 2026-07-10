---
id: kraken
type: exchange
nom: Kraken
sigle: KR
accent: "#8a8f98"
statut: rejete
tagline: "Évalué puis rejeté — un choix de jugement, pas un échec"
marches: ["—"]
stats:
  - { val: "2 API", label: "Spot et Futures totalement séparées" }
  - { val: "non-USDT", label: "futures libellés différemment" }
  - { val: "rejeté", label: "faible apport pour un coût élevé" }
schema_svg: null         # pas de schéma (non implémenté)
visuel: null
---

## Rôle

Kraken a été évalué comme 7e exchange puis écarté. Le présenter honnêtement montre
qu'intégrer n'est pas toujours la bonne décision.

## Résultats

- Décision documentée dans `docs/conception.md` §7.
- Le pipeline reste prêt à accueillir tout exchange au même format (BaseConnector + fiche docs).

## Embûches & solutions

### Spot et Futures = deux API séparées
- **Symptôme** — Aucune cohérence d'endpoints/conventions entre les deux marchés Kraken.
- **Cause** — Architecture historique de Kraken (Kraken Pro vs Kraken Futures).
- **Solution** — Double effort d'intégration pour un apport marginal → rejet.

### Futures non-USDT
- **Symptôme** — Les perps Kraken ne s'alignent pas sur le périmètre USDT du projet.
- **Cause** — Libellés/quotités différents.
- **Solution** — Hors périmètre hybride-Futures USDT → écarté.
