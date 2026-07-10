# Stratégie avancée (capstone) — réunir TOUT le cours dans un seul algorithme :
# régime + momentum + sizing au risque + stop suiveur + take-profit.
#   - RÉGIME  : close > SMA 200 -> on n'achète JAMAIS sous la moyenne de fond.
#   - ENTRÉE  : MACD(12,26,9) au-dessus de son signal ET RSI(14) > 50.
#   - TAILLE  : on risque 1 % du capital par trade ; la distance au stop (2xATR)
#               fixe la quantité — plus le marché est nerveux, plus la position est petite.
#   - SORTIES : stop suiveur 2xATR sous le plus haut close depuis l'entrée,
#               take-profit à l'entrée + 4xATR (ratio 2:1), sortie si le régime casse.
#   Long/flat -> comparable aux autres stratégies du cours.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_TENDANCE = 200      # SMA de régime (~8 jours en barres 1 h)
PERIODE_RSI = 14
SEUIL_RSI = 50
PERIODE_ATR = 14
STOP_MULT = 2.0             # stop suiveur : 2 x ATR sous le plus haut close
TAKE_MULT = 4.0             # take-profit : entrée + 4 x ATR (risque/rendement 2:1)
RISQUE_PAR_TRADE = 0.01     # 1 % du capital risqué par position


class BtcUsdtHourly(PythonData):
    """Lecteur custom validé (donnees.py), inchangé."""

    def get_source(self, config, date, is_live):
        return SubscriptionDataSource(DATA_FILE, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or not line[0].isdigit():
            return None
        cols = line.split(",")
        bar = BtcUsdtHourly()
        bar.symbol = config.symbol
        t_open = datetime.strptime(cols[0][:19], "%Y-%m-%d %H:%M:%S")
        bar.time = t_open
        bar.end_time = t_open + timedelta(hours=1)
        bar.value = float(cols[4])
        bar["open"] = float(cols[1])
        bar["high"] = float(cols[2])
        bar["low"] = float(cols[3])
        bar["close"] = float(cols[4])
        bar["volume"] = float(cols[5])
        return bar


class FraisTakerBinance(FeeModel):
    """0,04 % du notionnel (taker Binance USDⓈ-M) — inchangé depuis buyhold.py."""

    def get_order_fee(self, parameters):
        notionnel = abs(parameters.order.quantity) * parameters.security.price
        return OrderFee(CashAmount(notionnel * FRAIS_TAKER, "USD"))


class StrategieAvancee(QCAlgorithm):

    def initialize(self):
        with open(DATA_FILE) as f:
            rows = f.read().splitlines()
        premier = datetime.strptime(rows[1][:19], "%Y-%m-%d %H:%M:%S")
        dernier = datetime.strptime(rows[-1][:19], "%Y-%m-%d %H:%M:%S")
        self.set_start_date(premier.year, premier.month, premier.day)
        self.set_end_date(dernier.year, dernier.month, dernier.day)
        self.set_cash(CAPITAL)
        self.set_time_zone(TimeZones.UTC)

        proprietes = SymbolProperties("BTCUSDT perpetuel USDS-M", "USD", 1,
                                      0.1, 0.00000001, "BTCUSDT")
        heures = SecurityExchangeHours.always_open(TimeZones.UTC)
        securite = self.add_data(BtcUsdtHourly, "BTCUSDT", proprietes, heures,
                                 Resolution.HOUR)
        securite.set_fee_model(FraisTakerBinance())
        self.btc = securite.symbol

        # Quatre indicateurs, nourris à la main (transparence maximale).
        self.tendance = SimpleMovingAverage(PERIODE_TENDANCE)
        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
        self.macd = MovingAverageConvergenceDivergence(12, 26, 9,
                                                       MovingAverageType.EXPONENTIAL)
        self.atr = AverageTrueRange(PERIODE_ATR, MovingAverageType.WILDERS)

        # Mémoire du signe MACD-signal : l'entrée exige un CROISEMENT (événement),
        # pas un simple état « au-dessus » — sinon on ré-entre à la barre suivant
        # chaque stop et les frais explosent (mesuré : 174 ordres / 5 060 $ de frais
        # en version « état », contre 90 ordres / 2 679 $ en version « croisement »).
        self.diff_prec = None

        # État de la position (None / réinitialisé quand on est à plat).
        self.entry_prix = None
        self.stop_prix = None
        self.take_prix = None
        self.plus_haut = None
        self.raison = ""

        # Journal & mesure d'exposition.
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.sorties = {"STOP": 0, "TAKE": 0, "REGIME": 0}
        self.barres_total = 0
        self.barres_investi = 0
        self.premier_close = None
        self.dernier_close = None

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        bar = data[self.btc]
        close = float(bar.value)
        if self.premier_close is None:
            self.premier_close = close
        self.dernier_close = close

        # 1) Nourrir les indicateurs avec la barre qui vient de CLÔTURER.
        #    L'ATR a besoin du high/low -> on lui reconstruit une TradeBar complète.
        t = bar.end_time
        self.tendance.update(t, close)
        self.rsi.update(t, close)
        self.macd.update(t, close)
        tb = TradeBar(bar.time, self.btc, float(bar["open"]), float(bar["high"]),
                      float(bar["low"]), close, float(bar["volume"]), timedelta(hours=1))
        self.atr.update(tb)
        if not (self.tendance.is_ready and self.rsi.is_ready
                and self.macd.is_ready and self.atr.is_ready):
            return                                     # warmup : aucun signal

        atr = float(self.atr.current.value)
        regime_haussier = close > float(self.tendance.current.value)
        # Croisement MACD détecté sur TOUTES les barres prêtes (même en position),
        # sinon le signe mémorisé devient obsolète.
        diff = float(self.macd.current.value) - float(self.macd.signal.current.value)
        croise_haut = self.diff_prec is not None and self.diff_prec <= 0 and diff > 0
        self.diff_prec = diff
        investi = self.portfolio[self.btc].invested
        self.barres_total += 1
        if investi:
            self.barres_investi += 1

        # 2) SORTIES d'abord — le risque prime sur le signal.
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
                return                                 # en position et rien à signaler
            self.sorties[self.raison] += 1
            self.liquidate(self.btc)
            return

        # 3) ENTRÉE : régime haussier ET momentum confirmé — le MACD vient de
        #    CROISER au-dessus de son signal (événement) et le RSI valide (> 50).
        if not (regime_haussier and croise_haut
                and float(self.rsi.current.value) > SEUIL_RSI):
            return

        # 4) TAILLE : risquer 1 % du capital ; la distance au stop fixe la quantité.
        equite = float(self.portfolio.total_portfolio_value)
        distance_stop = STOP_MULT * atr
        quantite = (equite * RISQUE_PAR_TRADE) / distance_stop
        # Garde-fou : jamais plus que ~95 % du cash disponible (le risque théorique
        # peut réclamer plus de notionnel que le compte n'en a).
        quantite = min(quantite, 0.95 * float(self.portfolio.cash) / close)
        if quantite <= 0:
            return
        # Fixer stop/take AVANT market_order : le fill est synchrone, on_order_event
        # se déclenche pendant l'appel et journalise ces niveaux.
        self.plus_haut = close
        self.stop_prix = close - distance_stop
        self.take_prix = close + TAKE_MULT * atr
        self.market_order(self.btc, quantite)

    def on_order_event(self, event: OrderEvent):
        if event.status != OrderStatus.FILLED:
            return
        self.nb_trades += 1
        self.frais_totaux += float(event.order_fee.value.amount)
        if event.fill_quantity > 0:
            self.entry_prix = float(event.fill_price)
            self.log(f"TRADE {self.nb_trades:>2} ACHAT  {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"{float(event.fill_quantity):.6f} BTC @ {event.fill_price} | "
                     f"stop {self.stop_prix:,.0f} / take {self.take_prix:,.0f} | "
                     f"frais={event.order_fee.value.amount:.2f} $")
        else:
            gain = (float(event.fill_price) / self.entry_prix - 1) if self.entry_prix else 0.0
            self.log(f"TRADE {self.nb_trades:>2} VENTE  {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"[{self.raison:<6}] @ {event.fill_price} | P&L={gain:+.2%} | "
                     f"frais={event.order_fee.value.amount:.2f} $")
            self.entry_prix = None
            self.stop_prix = None
            self.take_prix = None
            self.plus_haut = None

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        exposition = self.barres_investi / self.barres_total if self.barres_total else 0.0
        self.log(f"--- BILAN STRATÉGIE AVANCÉE (régime + momentum + risque) ---")
        self.log(f"Trades : {self.nb_trades} | sorties : {self.sorties['STOP']} stop, "
                 f"{self.sorties['TAKE']} take, {self.sorties['REGIME']} régime | "
                 f"frais : {self.frais_totaux:.2f} $")
        self.log(f"Exposition : {exposition:.1%} des barres en position "
                 f"({self.barres_investi}/{self.barres_total})")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
