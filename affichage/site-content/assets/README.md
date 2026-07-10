# Contenu du site — Crypto Orderflow

Contenu **prêt à déposer** dans le projet de site dédié. Aucun code de site ici :
uniquement du **Markdown structuré** (texte + métadonnées) et des **SVG autonomes**.

```
site-content/
  pages/
    overview.md        page projet (hero, pitch, stats, stack)
    bitget.md          un fichier par exchange…
    binance.md
    bybit.md
    okx.md
    kucoin.md
    coinbase.md
    kraken.md          (statut: rejete — pas de schéma/visuel)
    hybride.md         vue hybride (recalage, 3 protections, phases)
    arbitrage.md       détection d'arbitrage
    architecture.md    briques transversales
  svg/
    bitget-carnet.svg … coinbase-carnet.svg   schéma init carnet (6, pas Kraken)
    recalage-hybride.svg                       schéma avant/après du recalage
```

## Format des pages

Chaque `.md` = **frontmatter YAML** (champs structurés) + **corps Markdown** (prose).
Les champs structurés (stats, embûches déjà séparées symptôme/cause/solution via les
sous-titres, protections, frais…) permettent à ton gabarit de rendre des cartes/tableaux
sans re-parser le texte.

### Champs frontmatter (exchanges)
| Champ | Sens |
|---|---|
| `id` | slug (= nom de route, ex. `okx`) |
| `nom`, `sigle`, `accent` | affichage (couleur d'accent par exchange) |
| `statut` | `reference` \| `actif` \| `actif-exclu` \| `rejete` → badge |
| `tagline`, `marches` | sous-titre + chips marché |
| `stats[]` | `{ val, label }` — bandeau de chiffres |
| `book_init` | `{ snapshot, updates, integrite }` — schéma init carnet |
| `schema_svg` | chemin du SVG d'init carnet (null si rejeté) |
| `visuel` | chemin de la capture à insérer (voir shot-list) |

Le corps contient `## Rôle`, `## Résultats` (liste), `## Embûches & solutions`
(chaque embûche = un `###` titre + 3 puces **Symptôme / Cause / Solution**).

Les pages `feature` (hybride/arbitrage/architecture) portent leurs données de cartes
dans le frontmatter (`protections`, `phases`, `fees`, `blocs`…) + une intro Markdown.

## Captures à produire (shot-list)

Lancer le dashboard (`python run_gui.py`), capturer en PNG (+ clips muets ~8-15 s si
possible). Noms attendus (référencés par le champ `visuel`) :

| Fichier | Contenu |
|---|---|
| `hybride_hero.png`   | money shot : vue hybride en live (overview) |
| `hybride_view.png`   | vue hybride (page Hybride) |
| `arbitrage_view.png` | graphe + marqueurs étoiles + barre d'état |
| `bitget_view.png` … `coinbase_view.png` | une vue par exchange |

Cohérence : même paire (BTC/USDT) et même résolution sur toutes les vues par exchange.

## SVG

Autonomes (fond sombre `#0b0e14` intégré, `xmlns` présent) → utilisables tels quels
comme `<img src>` ou inline. Couleur d'accent = celle de l'exchange. Si ton site est
sur fond clair, ils restent lisibles (fond sombre embarqué).
