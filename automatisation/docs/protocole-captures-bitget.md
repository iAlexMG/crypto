# Protocole de captures — automatisation crypto (Bitget démo)

Objectif : produire les **images et la vidéo** qui manquent au pilier « Automatisation »
du site (volet crypto). On démontre la **chaîne d'exécution live** sur la démo Bitget — pas
la rentabilité (cadrage fondamental du projet). Ce doc est le mode d'emploi de la prise ; il
se rouvre à chaque session de capture.

C'est **toi** qui arranges l'écran et qui captures (terminal + navigateur). Moi (Claude) je
fais le **post** : recadrage, floutage résiduel, annotation du fil, montage de la boucle
vidéo, puis intégration au site (`site-content/` → `sync-site.py` → push).

## Ce qui change par rapport aux indices (à lire une fois)

Le pilier indices se filme dans **Quantower** (chart + visuel OnPaintChart + panneau Ordres).
Ici, **il n'y a pas de Quantower ni de chart annoté** : la stratégie est un script Python et
l'exchange est un **site web**. Le cockpit crypto tient en **deux zones** :

1. le **terminal** — le runner `runner_sma.py --go` et/ou le suiveur `suivre-journal.ps1` ;
2. l'**interface web de la démo Bitget** — la preuve plateforme : position, bracket SL/TP,
   stop suiveur qui monte, fermeture.

Il n'y a donc **ni triptyque de visuels, ni chart à annoter**. La preuve se lit dans le
terminal (la décision) **et** dans l'UI Bitget (l'exécution réelle sur la démo).

## Décisions figées

- **Un seul écran 1080p** (1920×1080). Terminal à gauche, navigateur Bitget à droite (ou
  terminal en bandeau bas, navigateur plein cadre — au choix, voir §Cockpit).
- **Démo Bitget uniquement** (`paptrading:1`). Aucun ordre réel — c'est le cadrage du projet
  et la garde codée en dur du client (`allow_real=True` jamais posé).
- **Vidéo = boucle muette ~30–60 s**, autoplay/muted/loop ; le fil numéroté en surimpression
  (ajouté au montage) sert de narration.
- **La stratégie vedette de la vidéo = H2 (SMA Suiveur)** : le stop suiveur qui remonte
  marche par marche est le plan-clé (l'équivalent de l'escalier des indices), visible **à la
  fois** au terminal (`stop_modifie`) et dans l'UI Bitget (le SL du plan qui grimpe).
- **Le journal ne contient AUCUN numéro de compte** (contrairement à Apex) — rien à masquer
  au terminal. En revanche l'**UID Bitget** apparaît dans l'interface web : à masquer à la
  prise (voir §Lisibilité) ou je le floute en post.

## 1. Le cockpit — disposition 1080p

Deux dispositions possibles, les deux tiennent en 1080p :

- **A — côte à côte** : navigateur Bitget démo ~62 % à gauche (position + ordres bien lisibles),
  terminal ~38 % à droite. Recommandé pour les **crops fixes** (chaque zone est grande).
- **B — bandeau bas** : navigateur Bitget plein cadre en haut (~78 %), terminal en bandeau bas
  pleine largeur (~22 %). Recommandé pour la **vidéo** (l'Uch bouge, le terminal défile dessous).

Dans les deux cas, l'interface Bitget démo doit montrer, selon le moment :

- l'onglet **Positions** → *BTCUSDT · long (ou short) · prix d'entrée · marge · PnL · prix de liq* ;
- l'onglet **Ordres TP/SL** (ou « Plan/Trigger ») → le **SL et le TP attachés** (le bracket) ;
  pour H2, c'est **le trigger du SL qui monte** au fil des barres ;
- après le kill switch (Ctrl-C) ou une sortie : la position **disparaît** (retour à plat).

Le fil de lecture (annoté en post) : **1** signal (croisement, terminal) → **2** entrée +
bracket (terminal `entree`, puis position + SL/TP dans l'UI) → **3** stop suiveur qui monte
(terminal `stop_modifie`, SL qui grimpe dans l'UI, H2) → **4** sortie (terminal `sortie`,
position fermée dans l'UI).

## 2. Réglages de lisibilité (fait ou casse l'image)

- **Thème sombre** partout : le terminal (fond sombre, la palette du suiveur) **et** l'UI
  Bitget en thème sombre → cohérent avec le site (fond `#0B0E14`).
- **Polices agrandies** dans le terminal (zoom de la fenêtre PowerShell / Windows Terminal) :
  le texte doit survivre à la réduction à ~1200 px de large. Zoom aussi le navigateur (Ctrl+`+`)
  pour que la position et les ordres soient gros.
- **Capture en 1920×1080 natif** — jamais une fenêtre réduite puis agrandie.
- **Déclutter** : dans le navigateur, ferme les panneaux hors-sujet (graphique de trading en
  chandelles inutile ici, watchlist, chat) ; garde Positions + Ordres. Dans le terminal, une
  seule fenêtre, le suiveur ou le runner (pas les deux si l'écran est serré).
- **Masquage de l'UID Bitget** : l'en-tête du compte démo affiche un identifiant. Replie/masque
  la barre de compte, ou laisse-le et je le floute en post. (Rappel : le **journal** n'a pas
  d'UID, donc le terminal est propre d'office.)
- **Bandeau « démo »** : c'est un atout, pas un défaut — laisse visible la mention *Demo /
  Simulated* de Bitget. Le projet assume la démo (honnêteté = argument pour un recruteur).
- **Palette = celle du suiveur** (rien à réinventer) : SIGNAL cyan, ENTRÉE blanc, STOP ^
  magenta, SORTIE rouge (TP vert). Réutilisée dans l'annotation → l'œil relie image et légende.

## 3. Le terminal — `suivre-journal.ps1`

Dans `automatisation/captures/`. Lit le `.ndjson` du jour en direct et l'affiche proprement
(couleurs, heure UTC, UTF-8 géré). Le fichier reste un vrai `.ndjson` lu tel quel → c'est le
**même format que le jumeau** (`jumeau_hybrides.py`), donc la preuve de parité tient.

```powershell
# 1) lance la stratégie (crée le fichier du jour et exécute sur la démo) :
cd Portfolio\crypto\automatisation
python runner_sma.py --strategie h2 --go            # H2 suiveur, SMA 3/9 (défaut)
#   (option plus de croisements, si besoin : --rapide 2 --lente 6)

# 2) dans une 2e fenêtre, le suiveur lisible (ou double-clic sur Suivre-Journal-H2.bat) :
.\captures\suivre-journal.ps1 H2                     # H1=sma_bracket, H2=sma_suiveur, H3=sma_annule
.\captures\suivre-journal.ps1 -Fichier '..\journaux\sma_suiveur\2026-07-23.ndjson' -Instantane
```

**Raccourci un-geste** : `captures\Filmer-H2.bat` (double-clic) lance **le runner ET le suiveur
d'un coup**, chacun dans sa fenêtre (le suiveur en écran vierge, `-Neuf`). C'est le lanceur de
prise. `Suivre-Journal-H2.bat` ne lance QUE le suiveur (utile si le runner tourne déjà ailleurs,
ou pour rejouer un journal). Les deux restent deux fenêtres séparées : deux flux live ne se
mélangent pas proprement dans une seule console — d'où l'idée « une console = un flux ».

Deux façons de filmer le terminal :

- **le runner lui-même** — montre le *battement de cœur* (une ligne `·` par barre : close,
  SMA, écart, ATR, position) + les événements. Vivant, prouve que ça tourne en continu.
- **le suiveur** — plus lisible, épuré, coloré, une ligne par décision. Meilleur pour les crops.

Rendu du suiveur (extrait réel, H3 du 07-23) :

```
  16:39:00Z  SIGNAL       @64761.9 croisement haussier -> long   [ long  sma 64752.9/64743.8  atr 28.5 ]
  16:39:00Z  ENTRÉE       @64761.9 market long + bracket   [ long  SL 64719.1  TP 64847.5 ]
  16:42:00Z  SORTIE       @64719.1 bracket serveur refermé (SL/TP)   [ code SLTP ]
```

Chemins réels : `automatisation/journaux/<slug>/<AAAA-MM-JJ UTC>.ndjson`. Un sous-dossier par
stratégie (`sma_bracket`, `sma_suiveur`, `sma_annule`), un fichier par jour UTC.

## 4. Scénario de la vidéo (30–60 s, boucle muette) — H2

Enregistre en **1080p avec OBS Studio** (sortie mp4 propre). Un cycle H2 suffit :

| t | À l'écran (terminal + UI Bitget) | Beat |
|---|---|---|
| 0–5 s | battement de cœur qui défile, position à plat | plan large |
| ~5 s | **croisement SMA 3/9** → `SIGNAL` au terminal | **1** |
| ~7 s | `ENTRÉE` au terminal → **position LONG apparaît** + **SL attaché** dans l'UI Bitget | **2** |
| 8–40 s | `STOP ^` défile marche par marche (suiveur) ; dans l'UI, **le trigger du SL monte** | **3** |
| fin | `SORTIE` (stop touché ou croisement inverse) → **position fermée** dans l'UI | **4** |

Note : le stop suiveur **3** n'existe que pour **H2**. Pour H1/H3 (bracket fixe), le fil est
1 → 2 → 4 (entrée + bracket, puis sortie SL/TP ou annulation au croisement inverse).

## 5. Les crops fixes de détail

Depuis la même session (ou 2–3 essais) :

- **A — fill LONG** : entrée longue remplie dans l'UI Bitget (position + prix d'entrée) **et**
  la ligne `ENTRÉE` au terminal, avec le **bracket SL/TP** attaché visible dans l'UI.
- **B — fill SHORT** : idem en vente à découvert (SL au-dessus / TP en-dessous) — prouve les
  deux sens (one-way : `sell` depuis plat ouvre un short).
- **C — stop suiveur déplacé** (H2) : l'UI Bitget montrant le **trigger du SL remonté** au-delà
  du SL d'ouverture (le niveau traîné), à côté de la rafale de `STOP ^` au terminal.
- **D — kill switch** : la position ouverte, puis **Ctrl-C** au terminal (`ARRÊT`) → position
  **fermée** dans l'UI (avant / après). Prouve le coupe-circuit.

Bonus si tu veux (optionnels) : le **garde-fou dislocation** (`REFUSÉ` au terminal, une entrée
non prise) ; la sortie sur **croisement inverse** (H3, `SORTIE code=SIGNAL`).

## 6. Livraison et post-traitement

Tu déposes dans un dossier (à convenir — scratchpad ou `automatisation/captures/brut/`) : la
**vidéo brute** + les **PNG bruts**. Je fais : recadrage/nettoyage, floutage de l'UID Bitget
si besoin, annotation du fil 1→4, montage de la boucle (coupe, mute, surimpression, mp4 web
léger), puis intégration dans `Portfolio/crypto/site-content/` (pilier `automatisation`,
items `figure`) → `sync-site.py crypto` → push.

## Pièges — à relire avant chaque prise

- **Démo, pas réel** : lance bien `--go` **sans** toucher à la garde `allow_real`. Vérifie la
  mention *Demo* de Bitget à l'écran. Un ordre réel serait hors cadrage.
- **UNE stratégie à la fois** : les 3 tradent BTCUSDT — deux runners en `--go` se
  marcheraient dessus (positions mêlées). Ferme l'un avant de lancer l'autre.
- **Encodage** : PowerShell 5.1 lit un `.ndjson` UTF-8 en CP1252 → accents cassés à l'écran.
  Le suiveur force `-Encoding UTF8` ; si tu tail le JSON brut, ajoute `-Encoding UTF8`.
- **UID Bitget dans l'UI** : le journal est propre, mais l'interface web montre l'identifiant
  du compte démo — masque-le à la prise ou signale-le-moi pour le floutage.
- **Stop suiveur = H2 seulement** ; l'annulation au croisement inverse = H3 ; le bracket fixe
  qui referme = H1. Choisis la stratégie selon le plan que tu veux montrer.
- **Fonds virtuels** : le compte démo doit être approvisionné (10 000 USDT virtuels réclamés
  dans l'UI démo) sinon « marge insuffisante » à l'entrée.

## Renvois

Mémoire : `ialexmg-crypto-automatisation-bitget` (POC des 3 hybrides, chaîne prouvée),
`ialexmg-automatisation-hybrides` (le pendant indices), `ialexmg-audit-editorial-suivi`
(le pilier sur le site), `ialexmg-site-public-cible`, `terminologie-venue-exchange`,
`powershell-ps1-bom-cp1252`, `style-quebecois-anti-ia`.
Code : `runner_sma.py` (le runner + battement de cœur), `hybrides.py` (le moteur des 3),
`bitget_trading.py` (client signé, garde démo), `basis_dislocation.py` (garde-fou).
Prompt de reprise du pilier : `Claude_Code/Prompt_Automatisation_Bitget.md`.
