# Leçon 05 — Le retour à la moyenne : RSI et bandes de Bollinger

Le camp opposé de la leçon 04 : le **retour à la moyenne** (*mean reversion*). Après un excès,
le prix aurait tendance à revenir vers sa moyenne — donc on **achète la faiblesse** (quand
tout le monde vend, un rebond est « dû ») et on **vend la force**. On le construit avec deux
outils : le **RSI** (seuils **fixes** 30/70 sur un oscillateur borné) puis les **bandes de
Bollinger** (seuils **adaptatifs** : ± 2 écarts-types autour de la moyenne). Même cadre
long/flat, mêmes frais, mêmes données que les leçons précédentes — seule la thèse change.

Verdict mesuré d'avance : sur nos six mois baissiers, **les deux perdent lourdement**, et
chacune expose un défaut structurel différent — le RSI n'a **aucun stop** (un seul trade
efface 27 000 $), Bollinger **sur-trade** (155 ordres, ~5 % du capital en frais).

## Le RSI : des seuils fixes sur un oscillateur borné

Le **RSI** (*Relative Strength Index*) mesure la vigueur relative des hausses face aux baisses
sur les 14 dernières barres, comprimée dans un intervalle **borné 0-100**. C'est la différence
de nature avec une moyenne mobile (non bornée, elle vaut ce que vaut le prix) : borné, il
autorise des **seuils absolus** — RSI < 30 = survente (« soldé » → achat), RSI > 70 = surachat
(« cher » → vente). Cœur de
[`rsi_retour_moyenne.py`](../../../backtests/algorithms/rsi_retour_moyenne.py) (squelette
inchangé — seul le signal change) :

```python
PERIODE_RSI = 14          # période classique du RSI
SURVENTE = 30             # RSI < 30 -> ACHAT (pari de rebond)
SURACHAT = 70             # RSI > 70 -> VENTE (pari de repli)

    def initialize(self):
        # … (identique à buyhold.py : bornes, cash, UTC, SymbolProperties, frais) …
        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
        self.rsi_prec = None      # valeur précédente, pour détecter le FRANCHISSEMENT

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        close = float(data[self.btc].value)
        self.rsi.update(data[self.btc].end_time, close)
        if not self.rsi.is_ready:
            return

        rsi = float(self.rsi.current.value)
        if self.rsi_prec is not None:
            entre_survente = self.rsi_prec >= SURVENTE and rsi < SURVENTE   # plonge sous 30
            entre_surachat = self.rsi_prec <= SURACHAT and rsi > SURACHAT   # franchit 70
            if entre_survente and not self.portfolio[self.btc].invested:
                self.set_holdings(self.btc, 1.0)
            elif entre_surachat and self.portfolio[self.btc].invested:
                self.liquidate(self.btc)
        self.rsi_prec = rsi
```

`rsi_prec` est la jumelle du `diff_prec` de la leçon 04 : on agit au **franchissement** d'un
seuil, pas tant que le RSI *reste* dans la zone (sinon on rachèterait chaque barre sous 30).
Le warmup est court : 14 barres, contre 200 pour la SMA lente.

```powershell
dotnet QuantConnect.Lean.Launcher.dll `
    --algorithm-language Python `
    --algorithm-type-name RsiRetourMoyenne `
    --algorithm-location "../../../../algorithms/rsi_retour_moyenne.py"
```

Résultat mesuré (le journal logue le RSI à chaque fill — la logique est traçable) :

```
TRADE  1 ACHAT 2026-01-06 18:00 UTC | RSI=27.4 | qté=+1.08916235 @ 91547.6 | frais=39.88 $
TRADE  2 VENTE 2026-01-12 03:00 UTC | RSI=75.5 | qté=-1.08916235 @ 92168.8 | frais=40.15 $
TRADE  3 ACHAT 2026-01-19 00:00 UTC | RSI=24.1 | qté=+1.07147456 @ 93614.0 | frais=40.12 $
TRADE  4 VENTE 2026-02-13 16:00 UTC | RSI=70.6 | qté=-1.07147456 @ 68652.0 | frais=29.42 $
…
--- BILAN RSI 14 (30/70) ---
Trades exécutés : 31 | frais totaux : 1029.64 $
Équité finale : 73553.79 $ | rendement stratégie : -26.4462%

STATISTICS:: Net Profit -26.446%    STATISTICS:: Drawdown 33.900%
```

31 ordres — nombre **impair** : 16 achats, 15 ventes, la stratégie **finit investie** (le
backtest se termine avant que le RSI ne repasse 70). Et regarde TRADE 3 → TRADE 4 : acheté à
**93 614**, revendu à **68 652**. On y revient.

### Le face-à-face triple

| | Buy & Hold | SMA 50/200 (tendance) | **RSI (contre-tendance)** |
|---|---|---|---|
| Net Profit | −33,2 % | **−4,3 %** | −26,4 % |
| Drawdown max | 40,2 % | **16,5 %** | 33,9 % |
| Trades / Frais | 1 / 40 $ | 22 / 827 $ | 31 / **1 030 $** |
| Sharpe | — | −0,46 | −0,99 |

Sur ce marché baissier, **la contre-tendance est de loin la pire logique active** : elle ne
bat le Buy & Hold que de ~7 points (contre +29 pour la tendance), avec un drawdown presque
aussi violent. Pourquoi ? « Acheter la survente » revient à **acheter pendant que ça chute**.
En marché sans tendance, ça capte les rebonds ; dans une vraie tendance baissière, la survente
n'annonce pas un rebond — elle annonce **plus de baisse**.

![Trois panneaux alignés : le prix avec les achats dans les creux et les ventes sur les pics,
les périodes de détention ombrées ; l'oscillateur RSI entre 0 et 100 avec ses zones 30/70 ;
l'équité qui s'effondre en janvier-février puis se stabilise, drawdown maximal
33,9 %.](../assets/images/lecon-05-rsi.png)

*Figure — « Acheter bas, vendre haut » est visuellement séduisant (panneau du milieu) — mais
l'équité (bas) s'effondre dès janvier, quand un achat en survente est tenu pendant tout le
krach. (Script : [`fig_rsi.py`](../scripts/fig_rsi.py))*

### Anatomie du trade catastrophe

Une moyenne (−26 %) ne dit rien de *la façon* dont on perd. Isole TRADE 3 → TRADE 4 :

```
Achat  : 2026-01-19 00:00 | RSI 24.1 | 1.07147456 BTC @ 93 614.0
Vente  : 2026-02-13 16:00 | RSI 70.6 | 1.07147456 BTC @ 68 652.0
Durée  : 25 jours en position
Résultat brut : (68 652.0 − 93 614.0) × 1.07147456 = −26 746 $
```

**Un seul trade a effacé ~27 000 $** — l'essentiel de la perte totale. Le scénario : le RSI
plonge sous 30, on achète « la solde » à 93 614 ; le krach de février commence ; le RSI
**reste collé en zone basse** pendant des semaines (le prix ne fait que baisser) → aucun
signal de vente ; on tient le couteau qui tombe jusqu'au rebond de mi-février, sortie 25 %
plus bas. C'est le **défaut structurel** de la contre-tendance nue : la sortie « au RSI 70 »
suppose que le rebond viendra bientôt — hypothèse fausse en tendance forte, et **aucun stop**
pour couper. La leçon 06 (gestion du risque) montrera ce qu'un stop-loss aurait changé.

## Les bandes de Bollinger : des seuils qui respirent

Le défaut de conception du RSI : ses seuils **30/70 sont figés**. « Survente » veut dire la
même chose un jour calme et en plein krach — or un mouvement de −2 % est énorme dans le
premier cas, insignifiant dans le second. Les **bandes de Bollinger** rendent le seuil
**adaptatif** : une bande médiane (SMA 20), une bande haute (médiane + 2σ) et une bande basse
(médiane − 2σ). L'écart-type mesure la dispersion : marché agité → bandes écartées ; marché
calme → bandes resserrées. Un prix sous la bande basse est « anormalement faible **compte tenu
de sa propre agitation récente** ».

Stratégie : acheter quand le close casse **sous la bande basse**, sortir au **retour à la
médiane** (le retour à la moyenne, littéralement). Cœur de
[`bollinger.py`](../../../backtests/algorithms/bollinger.py) :

```python
PERIODE_BB = 20           # fenêtre de la moyenne et de l'écart-type
K_ECARTS = 2.0            # largeur des bandes = 2 écarts-types (le classique)

    def initialize(self):
        # … (identique aux autres stratégies) …
        self.bb = BollingerBands(PERIODE_BB, K_ECARTS, MovingAverageType.SIMPLE)

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        close = float(data[self.btc].value)
        self.bb.update(data[self.btc].end_time, close)
        if not self.bb.is_ready:
            return

        basse = float(self.bb.lower_band.current.value)
        mediane = float(self.bb.middle_band.current.value)
        investi = self.portfolio[self.btc].invested
        if not investi and close < basse:          # casse SOUS la bande basse -> ACHAT
            self.set_holdings(self.btc, 1.0)
        elif investi and close >= mediane:          # REVENU à la médiane -> SORTIE
            self.liquidate(self.btc)
```

Pas besoin de mémoire du pas précédent ici : `close < basse` compare le prix à une **référence
mobile**, et le garde `invested` évite de racheter en boucle. Note la sortie « à la médiane » —
un objectif *proche*. Retiens ce détail : c'est lui qui multiplie les trades.

```powershell
dotnet QuantConnect.Lean.Launcher.dll `
    --algorithm-language Python `
    --algorithm-type-name Bollinger `
    --algorithm-location "../../../../algorithms/bollinger.py"
```

```
TRADE  1 ACHAT 2026-01-06 17:00 UTC | largeur_bandes=1 | qté=+1.07618781 @ 92651.3 | frais=39.88 $
TRADE  2 VENTE 2026-01-06 23:00 UTC | largeur_bandes=3 | qté=-1.07618781 @ 93320.0 | frais=40.17 $
…
--- BILAN Bollinger 20 / 2.0σ ---
Trades exécutés : 155 | frais totaux : 4964.41 $
Équité finale : 68506.58 $ | rendement stratégie : -31.4934%

STATISTICS:: Net Profit -31.493%    STATISTICS:: Drawdown 37.300%
```

**155 ordres** — cinq fois le RSI — et **4 964 $ de frais, ~5 % du capital**. Le journal logue
la **largeur de bande** (`(haute − basse) / médiane`, en % du prix) : elle grimpe à 15 au
moment des fills du krach de février (le pic continu, visible sur la figure, atteint 18) et
retombe à 1 en marché calme — c'est une mesure **directe** du régime de volatilité.

### Le sur-trading démasqué

| | Buy & Hold | SMA | RSI | **Bollinger** |
|---|---|---|---|---|
| Net Profit | −33,2 % | −4,3 % | −26,4 % | **−31,5 %** |
| Trades / Frais | 1 / 40 $ | 22 / 827 $ | 31 / 1 030 $ | **155 / 4 964 $** |
| Sharpe | — | −0,46 | −0,99 | **−1,65** |

Le calcul décisif : **combien resterait-il sans les frais ?** 4 964 $ ≈ 5 points de
rendement ; même en les annulant, Bollinger ferait ~−26 % — comme le RSI. La stratégie cumule
donc **deux défauts indépendants** : (1) **pas d'edge** en tendance — acheter les creux d'une
descente perd à chaque palier, adaptatif ou non ; (2) **le sur-trading** — la sortie à la
médiane referme les trades très vite, on rouvre sans cesse, et 155 × ~0,08 % l'aller-retour
transforment un mauvais −26 % en −31,5 %. Toujours estimer le résultat **brut** : il sépare
« pas d'avantage » de « bon signal mangé par les coûts ». Ici, les deux.

![Trois panneaux alignés : le prix encadré par les bandes de Bollinger avec un mur dense de
triangles d'achat/vente ; la largeur de bande avec un pic à 18 % au krach de février et des
creux à 1 % ; l'équité qui s'érode jusqu'à −31,5 %.](../assets/images/lecon-05-bollinger.png)

*Figure — L'enveloppe respire avec la volatilité (milieu), mais le prix touche ses bandes si
souvent que les trades s'empilent (mur de triangles, haut) ; chaque aller-retour coûte
~0,08 %, et l'équité fond même quand les paris individuels sont à peu près neutres. (Script :
[`fig_bollinger.py`](../scripts/fig_bollinger.py))*

Dernière observation, qui prépare la suite : dans le journal, les pires trades coïncident avec
une **largeur de bande élevée** (acheter la faiblesse en plein krach), les petits gains avec
une largeur de 1-3 (marché calme). **La volatilité n'est pas un décor, c'est un signal** — on
peut filtrer les entrées par régime, dimensionner les positions par la volatilité (leçon 06),
ou espacer les sorties pour couper le sur-trading. Le capstone (leçon 08) exploitera tout ça.

## Ce qu'il faut retenir

- **Oscillateur borné → seuils fixes possibles** (RSI 30/70) ; **enveloppe ± kσ → seuils
  adaptatifs** (Bollinger). Deux constructions du même pari : le retour à la moyenne.
- **En marché en tendance, la contre-tendance saigne** : la survente annonce *plus* de baisse.
  RSI −26,4 %, Bollinger −31,5 %, contre −4,3 % pour la tendance — même marché, mêmes frais.
- **Une stratégie de signal nue n'a pas de gestion du risque** : le RSI a tenu un couteau qui
  tombe 25 jours (−27 000 $ sur un seul trade). Une moyenne cache la distribution — lis le
  journal, pas seulement le Net Profit.
- **Le sur-trading est un défaut de conception, pas un détail** : un objectif de sortie proche
  = des trades courts = des frais qui s'empilent (155 × 0,04 % ≈ 5 % du capital). Estime
  toujours le brut hors frais pour séparer les deux maladies.

---

*Précédent : [Leçon 04 — Suivre la tendance](lecon-04-tendance-sma-macd.md) · Suite :
[Leçon 06 — La gestion du risque](lecon-06-gestion-risque.md) : greffer stop-loss et
take-profit sur le RSI — et découvrir un paradoxe.*
