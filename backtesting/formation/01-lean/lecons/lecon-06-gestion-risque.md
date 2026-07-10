# Leçon 06 — La gestion du risque : séparer le signal de la survie

Les leçons 04 et 05 cherchaient le bon **signal**. Aucune stratégie n'a proprement battu le
Buy & Hold. Le pro sait pourquoi : un backtest se gagne autant par la **gestion du risque**
que par le signal. Le signal dit *quoi* faire ; le risque dit *combien engager* et *quand
couper*, indépendamment de ce que crie le signal. Trois leviers, tous séparés de la logique de
signal : le **stop-loss** (perte maximale tolérée par trade), le **take-profit** (gain qu'on
sécurise), et le **dimensionnement** (*sizing* — jusqu'ici on faisait `set_holdings(1.0)`,
un pari à 100 % jamais questionné).

Protocole de la leçon : on garde **exactement** le signal RSI de la leçon 05 — celui du trade
catastrophe à −27 000 $ — et on ajoute par-dessus une couche stop/take. Si le résultat change,
ce n'est pas le signal : c'est la preuve que le risque compte autant que lui. Le verdict est
un paradoxe.

## Le code

[`risque_stops.py`](../../../backtests/algorithms/risque_stops.py) : le signal est copié de
`rsi_retour_moyenne.py`, la couche de risque s'insère dans `on_data`, évaluée **avant** le
signal et **prioritaire** sur lui :

```python
STOP = 0.08               # coupe à -8 % de l'entrée
TAKE = 0.10               # encaisse à +10 %

    def on_data(self, data: Slice):
        # … (mise à jour du RSI, identique à rsi_retour_moyenne.py) …
        investi = self.portfolio[self.btc].invested

        # ── RISQUE (prioritaire) : n'agit que si on est en position ──
        if investi and self.entry_prix is not None:
            if close <= self.entry_prix * (1 - STOP):
                self.raison = "STOP";  self.liquidate(self.btc)
            elif close >= self.entry_prix * (1 + TAKE):
                self.raison = "TAKE";  self.liquidate(self.btc)

        # ── SIGNAL (inchangé) : RSI 30 -> achat, RSI 70 -> vente ──
        investi = self.portfolio[self.btc].invested   # relire : le risque a pu liquider
        if self.rsi_prec is not None:
            if (self.rsi_prec >= SURVENTE and rsi < SURVENTE) and not investi:
                self.set_holdings(self.btc, 1.0)
            elif (self.rsi_prec <= SURACHAT and rsi > SURACHAT) and investi:
                self.raison = "SIGNAL";  self.liquidate(self.btc)
        self.rsi_prec = rsi
```

Trois détails qui font la rigueur : la couche de risque est testée **d'abord** (un stop touché
coupe avant que le signal n'ait son mot à dire) ; `entry_prix` — fixé sur le fill d'achat dans
`on_order_event` — est la **mémoire de risque** que le signal ne connaît pas (deux logiques
vraiment séparées) ; et on **relit `invested`** après la couche de risque, sinon le signal
agirait sur un état périmé.

## Le stop coupe le désastre…

```powershell
dotnet QuantConnect.Lean.Launcher.dll `
    --algorithm-language Python `
    --algorithm-type-name RisqueStops `
    --algorithm-location "../../../../algorithms/risque_stops.py"
```

D'abord **le** trade qui nous intéresse — le catastrophe de la leçon 05 :

```
TRADE  3 ACHAT  2026-01-19 00:00 UTC | RSI=24.1 | @ 93614.0 | frais=40.12 $
TRADE  4 VENTE  2026-01-29 16:00 UTC | [STOP  ] | @ 84898.0 | P&L=-9.31% | frais=36.39 $
```

Sans stop, ce trade était tenu jusqu'au 13 février à 68 652 : **−26,7 %** (≈ −27 000 $). Avec
le stop, il sort le 29 janvier à **−9,3 %**. Le garde-fou a fait exactement son travail :
couper le couteau qui tombe.

## …mais le total est pire : le paradoxe

```
--- BILAN RSI+RISQUE (stop 8% / take 10%) ---
Trades : 41 | sorties: 6 stop, 0 take, 14 signal | frais : 1320.60 $
Équité finale : 71049.04 $ | rendement stratégie : -28.9510%
```

| | RSI nu | **RSI + stop/take** |
|---|---|---|
| Net Profit | −26,4 % | **−29,0 %** |
| Drawdown max | 33,9 % | 34,1 % |
| Trades / Frais | 31 / 1 030 $ | **41 / 1 321 $** |
| Sharpe | −0,99 | **−1,30** |

**Le stop a économisé 18 points sur le pire trade et rendu le résultat global pire.** Comment ?

Parce que le RSI est une stratégie de **retour à la moyenne** : son pari est que « ce qui a
chuté va rebondir ». Un stop-loss vend **précisément le creux** — l'instant où le rebond est
le plus probable. Il **combat l'edge** de la stratégie. Sur les 6 stops déclenchés, plusieurs
coupent juste avant que le prix ne reparte, transformant des pertes temporaires en pertes
**réalisées** — puis on re-rentre au signal suivant (d'où 41 trades au lieu de 31, et 300 $ de
frais en plus).

C'est **la** leçon, contre-intuitive : **un stop n'est pas gratuit**. Il épouse la logique
d'une stratégie de **tendance** (couper vite les perdants, laisser courir les gagnants — c'est
son terrain naturel, le capstone de la leçon 08 l'utilisera ainsi), mais il **contrarie** une
stratégie de **contre-tendance**. « Séparer le signal du risque » ne veut pas dire « ajouter
un stop et espérer » : cela veut dire **accorder le risque au type de signal**. Et surtout :
l'intuition « un stop aide toujours » est fausse — **seule la mesure tranche**.

![Deux panneaux : en haut les trades de la version avec risque, avec le trade catastrophe
annoté — coupé au stop à −9 % au lieu de −27 % ; en bas les deux courbes d'équité superposées,
RSI nu (pointillé) et RSI + stop/take (trait plein), la version nue finissant
au-dessus.](../assets/images/lecon-06-risque.png)

*Figure — Haut : le stop fait son travail sur le trade catastrophe. Bas : au niveau du
portefeuille, couper les creux d'une stratégie de retour à la moyenne rogne le total — le
risque doit **matcher** le signal. (Script : [`fig_risque.py`](../scripts/fig_risque.py))*

## Deux finesses de pro

**Le stop est discret.** Réglé à −8 %, le trade catastrophe sort à **−9,3 %**. La condition
n'est évaluée **qu'au close de chaque barre horaire** : entre deux closes, le prix a déjà
franchi le seuil, et on n'exécute qu'au close suivant, plus bas. Sur données horaires, un stop
« à −8 % » coupe en pratique un peu plus bas — c'est le *slippage de stop discret*, une limite
assumée de notre résolution. (LEAN sait aussi poser de **vrais** ordres
`stop_market_order` côté moteur, exécutés dès que le prix les touche — notre test au close est
un choix de transparence pédagogique.)

**Le sizing est le levier qu'on n'a jamais bougé.** `set_holdings(1.0)` depuis la leçon 03 :
un choix de risque **maximal**, implicite. Engager 50 % halverait pertes et gains et diviserait
le drawdown ; dimensionner selon la **volatilité** (moins quand le marché s'agite) est la
version pro — c'est conditionner le risque au régime, pas seulement couper après coup. C'est
exactement ce que fera la stratégie avancée de la leçon 08 (1 % de risque par trade, taillé
par l'ATR).

## Ce qu'il faut retenir

- **Signal ≠ risque** : deux couches séparées — le risque évalué d'abord, avec sa propre
  mémoire (`entry_prix`), et un état relu après coup.
- **Le stop coupe le pire trade** (−27 % → −9,3 %) **mais peut dégrader le total** (−26,4 % →
  −29,0 % ici) : sur une contre-tendance, il vend le creux que la stratégie veut détenir. Le
  risque s'accorde au type de signal — sur une tendance, le même stop épouse l'edge.
- **Un stop au close est un plancher approché**, pas une garantie : il sort sous le seuil, et
  d'autant plus que la résolution est grossière.
- **Le sizing est un levier de risque à part entière** : 100 % investi n'est pas « neutre »,
  c'est le pari maximal. Le moduler (fixe ou par volatilité) change tout le profil.

---

*Précédent : [Leçon 05 — Le retour à la moyenne](lecon-05-retour-moyenne-rsi-bollinger.md) ·
Suite : [Leçon 07 — Métriques et optimisation](lecon-07-metriques-optimisation.md) : lire un
rapport comme un pro, s'en méfier, et reconnaître un backtest trop beau pour être vrai.*
