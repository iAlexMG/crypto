# Leçon 08 — La stratégie avancée : tout assembler, pour de vrai

Le moment d'assembler. Chaque leçon a livré une brique : le suivi de tendance limite la casse
mais la contre-tendance saigne (04-05), un filtre de régime écarte les entrées à contre-courant
(04), le risque se dimensionne et se coupe — à condition de matcher le signal (06), et les
paramètres se choisissent *a priori*, jamais après avoir vu la courbe (07). La stratégie de
cette leçon utilise **tout, en même temps**. C'est un vrai backtest final : règles écrites à
l'avance, un run mesuré, une analyse de sensibilité, et un verdict honnête.

## Les règles

**Univers** : BTCUSDT perp 1H, long/flat, mêmes données/frais que tout le cours. Puis quatre
couches, chacune héritée d'une leçon :

| Couche | Règle | Origine |
|---|---|---|
| **Régime** | on ne peut acheter que si `close > SMA 200` | leçon 04 : ne jamais jouer contre la tendance de fond |
| **Entrée** | le MACD(12,26,9) vient de **croiser** au-dessus de son signal **et** RSI(14) > 50 | leçon 04 : un croisement (événement), pas un état ; deux lectures qui se confirment |
| **Taille** | on risque **1 % du capital** par trade : `quantité = (équité × 1 %) / (2 × ATR)` | leçon 06 : le sizing est un levier de risque ; l'ATR (volatilité) taille la position |
| **Sorties** | stop **suiveur** à 2×ATR sous le plus haut close ; take-profit à l'entrée + 4×ATR (ratio 2:1) ; sortie si le régime casse (`close < SMA 200`) | leçon 06 : sur une stratégie de tendance, le stop épouse l'edge |

Deux principes de conception à remarquer. D'abord, **l'entrée est un signal, les sorties sont
du risque** : une fois en position, le MACD n'a plus voix au chapitre — seuls comptent le stop,
le take et le régime. Ensuite, le **sizing par l'ATR** inverse la logique du
`set_holdings(1.0)` : plus le marché est nerveux (ATR élevé), plus la position est **petite** —
le montant *risqué* (distance au stop × quantité) reste constant à 1 % du capital.

Les paramètres (200, 12/26/9, 14, 2×, 4×, 1 %) sont les conventions standard, posées **avant**
de lancer quoi que ce soit. Aucune optimisation — la leçon 07 a montré ce qu'il en coûte.

## Le code

Fichier complet : [`strategie_avancee.py`](../../../backtests/algorithms/strategie_avancee.py)
(squelette lecteur/frais/`SymbolProperties` inchangé). Le cœur :

```python
PERIODE_TENDANCE = 200      # SMA de régime (~8 jours en barres 1 h)
SEUIL_RSI = 50
STOP_MULT = 2.0             # stop suiveur : 2 x ATR sous le plus haut close
TAKE_MULT = 4.0             # take-profit : entrée + 4 x ATR (risque/rendement 2:1)
RISQUE_PAR_TRADE = 0.01     # 1 % du capital risqué par position

    def on_data(self, data: Slice):
        # … nourrir SMA 200, RSI, MACD (close) et ATR (TradeBar reconstruite : il
        #   a besoin du high/low) ; return tant que tout n'est pas is_ready …

        atr = float(self.atr.current.value)
        regime_haussier = close > float(self.tendance.current.value)
        # Croisement MACD détecté sur TOUTES les barres prêtes (même en position),
        # sinon le signe mémorisé devient obsolète.
        diff = float(self.macd.current.value) - float(self.macd.signal.current.value)
        croise_haut = self.diff_prec is not None and self.diff_prec <= 0 and diff > 0
        self.diff_prec = diff
        investi = self.portfolio[self.btc].invested

        # ── SORTIES d'abord : le risque prime sur le signal ──
        if investi:
            self.plus_haut = max(self.plus_haut, close)
            nouveau_stop = self.plus_haut - STOP_MULT * atr   # suiveur : ne fait que monter
            if nouveau_stop > self.stop_prix:
                self.stop_prix = nouveau_stop
            if close <= self.stop_prix:
                self.raison = "STOP"
            elif close >= self.take_prix:
                self.raison = "TAKE"
            elif not regime_haussier:
                self.raison = "REGIME"
            else:
                return
            self.sorties[self.raison] += 1
            self.liquidate(self.btc)
            return

        # ── ENTRÉE : régime haussier ET momentum confirmé ──
        if not (regime_haussier and croise_haut
                and float(self.rsi.current.value) > SEUIL_RSI):
            return

        # ── TAILLE : risquer 1 % du capital, la distance au stop fixe la quantité ──
        equite = float(self.portfolio.total_portfolio_value)
        distance_stop = STOP_MULT * atr
        quantite = (equite * RISQUE_PAR_TRADE) / distance_stop
        # Garde-fou : jamais plus que ~95 % du cash disponible.
        quantite = min(quantite, 0.95 * float(self.portfolio.cash) / close)
        if quantite <= 0:
            return
        # Fixer stop/take AVANT market_order : le fill est synchrone, on_order_event
        # se déclenche pendant l'appel et journalise ces niveaux.
        self.plus_haut = close
        self.stop_prix = close - distance_stop
        self.take_prix = close + TAKE_MULT * atr
        self.market_order(self.btc, quantite)
```

Deux subtilités LEAN gagnées en écrivant ce code : l'**ATR exige des barres complètes**
(high/low, pas seulement le close) — on lui reconstruit une `TradeBar` depuis notre donnée
custom ; et le **fill d'un `market_order` est synchrone** — `on_order_event` se déclenche
*pendant* l'appel, donc tout état que le handler journalise (stop, take) doit être fixé
*avant*.

## Exécution et résultat mesuré

```powershell
dotnet QuantConnect.Lean.Launcher.dll `
    --algorithm-language Python `
    --algorithm-type-name StrategieAvancee `
    --algorithm-location "../../../../algorithms/strategie_avancee.py"
```

Le journal montre le sizing à l'œuvre — la quantité varie avec la volatilité, et chaque sortie
a sa raison :

```
TRADE  1 ACHAT  2026-01-09 16:00 UTC | 0.797937 BTC @ 91137.7 | stop 89,884 / take 93,644 | frais=29.09 $
TRADE  2 VENTE  2026-01-09 19:00 UTC | [REGIME] @ 90772.3 | P&L=-0.40% | frais=28.97 $
TRADE  5 ACHAT  2026-01-13 06:00 UTC | 0.971183 BTC @ 91883.6 | stop 90,864 / take 93,923 | frais=35.69 $
TRADE  6 VENTE  2026-01-13 20:00 UTC | [TAKE  ] @ 94179.5 | P&L=+2.50% | frais=36.59 $
…
TRADE 90 VENTE  2026-06-17 20:00 UTC | [STOP  ] @ 64271.3 | P&L=-2.19% | frais=28.87 $

--- BILAN STRATÉGIE AVANCÉE (régime + momentum + risque) ---
Trades : 90 | sorties : 28 stop, 9 take, 8 régime | frais : 2679.00 $
Exposition : 13.8% des barres en position (572/4145)
Équité finale : 96748.50 $ | rendement stratégie : -3.2515%
Buy & Hold (close/close) : -33.2310% | écart stratégie - B&H : +29.9795%

STATISTICS:: Net Profit -3.252%      STATISTICS:: Drawdown 8.000%
STATISTICS:: Win Rate 31%            STATISTICS:: Profit-Loss Ratio 1.96
```

![Deux panneaux alignés : le prix avec la SMA 200 et 45 achats/45 ventes marqués au prix réel,
concentrés dans les phases où le prix est au-dessus de la moyenne ; l'équité quasi plate autour
de 96-100 k$ face au Buy & Hold qui plonge à 67 k$, drawdown maximal
8 %.](../assets/images/lecon-08-strategie-avancee.png)

*Figure — Les achats ne vivent que dans les poches de régime haussier (prix > SMA 200 orange) ;
les positions, taillées au risque, laissent l'équité presque insensible au krach. (Script :
[`fig_strategie_avancee.py`](../scripts/fig_strategie_avancee.py))*

Lecture honnête, dans l'ordre :

- **Le contrôle du risque est la vraie victoire : drawdown 8 %** — contre 16,5 % pour la SMA
  seule et 40 % en Buy & Hold — avec seulement 13,8 % du temps exposé au marché. La mécanique
  par trade fait exactement ce qu'elle promet : perte moyenne −0,82 %, gain moyen +1,61 %
  (ratio 1,96 ≈ le 2:1 de conception), aucun trade catastrophe.
- **Mais elle perd quand même** (−3,25 %), et le coupable est un vieil ennemi : **2 679 $ de
  frais, soit 2,7 points** — le résultat brut est proche de zéro (~−0,6 %). 45 aller-retours à
  0,08 % l'unité, voilà la facture. Le win rate de 31 % est *normal* pour une stratégie de
  tendance (beaucoup de petits stops, quelques bons trades) — mais à ce niveau de frais,
  l'expectancy reste légèrement négative.
- **Le régime a tenu sa promesse** : 8 sorties « REGIME » et, surtout, aucune position pendant
  les grandes jambes de baisse (regarde la figure : les bandes vertes n'existent que dans les
  poches haussières).

## L'ingénierie mesurée : un état n'est pas un événement

Première version écrite pour cette leçon : l'entrée sur l'**état** « MACD au-dessus de son
signal » (comme l'Exemple du manuel). Résultat mesuré : après chaque stop, la condition étant
toujours vraie, on ré-entrait **à la barre suivante** — 174 ordres, 5 060 $ de frais, −6,41 %,
drawdown 10,9 %. Le passage à l'entrée sur **croisement** (un événement, détecté par la mémoire
`diff_prec` de la leçon 04) sert de cooldown naturel :

| Entrée | Ordres | Frais | Net | Drawdown |
|---|---|---|---|---|
| état (MACD > signal) | 174 | 5 060 $ | −6,41 % | 10,9 % |
| **croisement (retenu)** | **90** | **2 679 $** | **−3,25 %** | **8,0 %** |

Une ligne de code, la moitié des frais. C'est la leçon 04 (MACD : 341 ordres) qui rejouait —
et qu'on n'a détectée qu'en **lisant le journal**, pas la courbe.

## Sensibilité : et si le stop était différent ?

Trois runs, tout égal par ailleurs :

| Stop suiveur | Ordres | Frais | Net | Drawdown |
|---|---|---|---|---|
| 1,5 × ATR | 104 | 3 557 $ | −5,44 % | 8,3 % |
| **2 × ATR (a priori)** | 90 | 2 679 $ | −3,25 % | 8,0 % |
| 3 × ATR | 80 | 1 681 $ | **+0,78 %** | **4,4 %** |

Le comportement est **monotone et explicable** : plus le stop est large, moins il se déclenche
(36 → 28 → 20 stops), moins on paie de frais de ré-entrée, et plus on laisse respirer les
trades gagnants. Pas de pic isolé — plutôt un plateau incliné : bon signe de robustesse
(leçon 07).

Et maintenant, le réflexe qui sépare l'amateur du pro : le 3×ATR est **positif** — faut-il le
retenir ? **Non.** Choisir 3× *parce qu'il a le meilleur résultat sur cet échantillon* serait
exactement le sur-apprentissage de la leçon 07 — un paramètre élu par les accidents de six
mois de données. On publie le résultat du paramètre choisi **a priori** (2×, −3,25 %), et la
sensibilité comme information de robustesse. Si le 3× devait être retenu, ce serait après
validation sur d'autres périodes et d'autres actifs — pas sur celle qui l'a désigné.

## Le tableau de bord final

![Courbes d'équité superposées des sept stratégies : la stratégie avancée (noir, −3,3 %) et la
SMA 50/200 (vert, −4,3 %) restent proches de 100 k$, toutes les autres — RSI, RSI+risque,
Bollinger, MACD, Buy & Hold — glissent entre 66 et 75 k$.](../assets/images/lecon-08-synthese.png)

*Figure — La section en une image : seules survivent les stratégies qui savent **sortir** (SMA)
ou **ne pas entrer** (stratégie avancée : régime + risque). (Script :
[`fig_synthese.py`](../scripts/fig_synthese.py))*

| Stratégie | Net | Drawdown | Trades | Frais | Ce qu'elle enseigne |
|---|---|---|---|---|---|
| **Stratégie avancée** | **−3,3 %** | **8,0 %** | 90 | 2 679 $ | régime + sizing au risque = la casse contrôlée |
| SMA 50/200 | −4,3 % | 16,5 % | 22 | 827 $ | rester à plat pendant les krachs protège |
| RSI 14 | −26,4 % | 33,9 % | 31 | 1 030 $ | acheter la faiblesse en tendance = couteau qui tombe |
| RSI+risque | −29,0 % | 34,1 % | 41 | 1 321 $ | un stop mal accordé au signal peut nuire |
| Bollinger | −31,5 % | 37,3 % | 155 | 4 964 $ | le sur-trading et la tyrannie des frais |
| MACD | −32,1 % | 33,1 % | 341 | 11 692 $ | rapide ≠ mieux ; paramètres accordés au timeframe |
| Buy & Hold | −33,2 % | 40,2 % | 1 | 40 $ | la référence à battre — dure à battre proprement |

**Le verdict honnête** : sur ces six mois baissiers, aucune stratégie longue ne crée de
richesse — la stratégie avancée fait « seulement » deux choses que les autres ne font pas :
elle divise la douleur par cinq (drawdown 8 % vs 40 %) et rend la perte résiduelle presque
entièrement imputable aux frais. C'est le résultat **mesuré**, et il vaut mille backtests
« +200 % » invérifiables.

Les limites restent celles du cours, **assumées** : fills au close du signal (optimiste),
slippage et funding non modélisés, stops discrets au close horaire, USDT ≡ USD, et surtout
**un seul actif, un seul régime** — rien ne garantit qu'une conclusion tienne en marché
haussier ou en range. Un backtest qui prétend tout modéliser ment sur au moins un de ces
points ; le nôtre dit exactement où sont ses bords.

## Ce qu'il faut retenir — de la leçon et de la section

- **Une stratégie complète est un empilement de couches** — régime, signal d'entrée
  (événement, pas état), sizing au risque, sorties de risque — chacune testable et mesurable
  séparément. L'entrée est un signal ; les sorties sont du risque.
- **Le contrôle du risque se voit dans le drawdown, pas dans le Net Profit** : 8 % contre 40 %,
  à exposition 13,8 %. Le sizing par l'ATR maintient le risque par trade constant (1 %) quelle
  que soit la nervosité du marché.
- **Les frais restent l'ennemi final** : 2,7 des 3,25 points de perte. Et une ligne de code
  (état → croisement) les divise par deux — le journal des trades reste le meilleur outil
  d'audit du cours.
- **Un paramètre choisi après coup est un mensonge** : le 3×ATR positif reste au tableau de
  sensibilité, pas en production. Robuste > performant, toujours.
- Et les cinq vérités transversales de la section, qui ne dépendent ni de LEAN ni du BTC :
  **l'exécution domine, les frais décident, le risque doit matcher le signal, les métriques
  seules mentent, le passé se sur-apprend.**

---

*La suite de la formation : rejouer tout ceci — même source de vérité, mêmes frais, mêmes
stratégies — sur le second moteur (**vectorbt**, section 02), puis **réconcilier** les deux au
centime (section 03). Le sol de vérité de la [leçon 03](lecon-03-buy-and-hold.md) sera le
premier test de parité ; le tableau de bord ci-dessus deviendra la colonne « LEAN » du
comparatif final. Deux moteurs indépendants qui tombent d'accord au centime : c'est le niveau
de rigueur d'un vrai pupitre quant.*

*Suite : [Leçon 09 — Le volume profile](lecon-09-volume-profile.md), une extension
order-flow : reconstruire depuis les ticks la dimension que l'OHLCV a écrasée.*
