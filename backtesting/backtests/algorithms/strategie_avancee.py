# Stratégie avancée (capstone) — SCALPING 1 m, multi-TF, long/short.
# Réunir TOUT le cours dans un seul algorithme : régime + momentum + sizing au
# risque + stop suiveur + take-profit, version scalping :
#   - RÉGIME  : filtre de fond en 5 m (SMA 100 ≈ 8 h) — on ne prend une position que
#               DANS le sens du régime (long si régime haussier, short si baissier).
#   - ENTRÉE  : le MACD(12,26,9) CROISE son signal dans le sens du régime ET le RSI
#               confirme (RSI > 50 pour un long, < 50 pour un short).
#   - TAILLE  : on risque 0,5 % du capital par trade ; la distance au stop (1,5xATR)
#               fixe la quantité — plus le marché est nerveux, plus la position est petite.
#   - SORTIES : stop SUIVEUR 1,5xATR (sous le plus haut close en long, au-dessus du
#               plus bas close en short), take-profit à 2,5xATR (R:R ≈ 1,67), sortie
#               si le régime 5 m casse. Long/short -> plus comparable au Buy & Hold.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
REGIME_5M = 100            # SMA de régime de fond sur barres 5 m (≈ 8 h) — filtre multi-TF
PERIODE_RSI = 9            # RSI court (aligné sur les autres stratégies scalping)
SEUIL_RSI = 50
PERIODE_ATR = 14
STOP_MULT = 1.5            # stop suiveur : 1,5 x ATR de l'extrême close depuis l'entrée
TAKE_MULT = 2.5            # take-profit : entrée ± 2,5 x ATR (R:R ≈ 1,67)
RISQUE_PAR_TRADE = 0.005   # 0,5 % du capital risqué par position


class BtcUsdt1m(PythonData):
    """Lecteur custom validé (donnees.py), inchangé."""

    def get_source(self, config, date, is_live):
        return SubscriptionDataSource(DATA_FILE, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or not line[0].isdigit():
            return None
        cols = line.split(",")
        bar = BtcUsdt1m()
        bar.symbol = config.symbol
        t_open = datetime.strptime(cols[0][:19], "%Y-%m-%d %H:%M:%S")
        bar.time = t_open
        bar.end_time = t_open + timedelta(minutes=1)
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
        securite = self.add_data(BtcUsdt1m, "BTCUSDT", proprietes, heures,
                                 Resolution.MINUTE)
        securite.set_fee_model(FraisTakerBinance())
        self.btc = securite.symbol

        # Indicateurs, nourris à la main (transparence maximale).
        # Le régime est un filtre 5 m : SMA nourrie du close aux bornes de 5 min.
        self.regime = SimpleMovingAverage(REGIME_5M)
        self.dernier_close_5m = None
        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
        self.macd = MovingAverageConvergenceDivergence(12, 26, 9,
                                                       MovingAverageType.EXPONENTIAL)
        self.atr = AverageTrueRange(PERIODE_ATR, MovingAverageType.WILDERS)

        # Mémoire du signe MACD-signal : l'entrée exige un CROISEMENT (événement),
        # pas un simple état « au-dessus » — sinon on ré-entre à la barre suivant
        # chaque stop et les frais explosent.
        self.diff_prec = None

        # État de la position (None / réinitialisé quand on est à plat).
        self.entry_prix = None
        self.stop_prix = None
        self.take_prix = None
        self.plus_haut = None     # extrême close depuis l'entrée (long -> plus haut)
        self.plus_bas = None      # extrême close depuis l'entrée (short -> plus bas)
        self.raison = ""

        # Journal & mesure d'exposition.
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.sorties = {"STOP": 0, "TAKE": 0, "REGIME": 0}
        self.barres_total = 0
        self.barres_investi = 0
        self.premier_close = None
        self.dernier_close = None

    def _regime(self):
        if not self.regime.is_ready or self.dernier_close_5m is None:
            return 0
        return 1 if self.dernier_close_5m > self.regime.current.value else -1

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        bar = data[self.btc]
        close = float(bar.value)
        if self.premier_close is None:
            self.premier_close = close
        self.dernier_close = close
        bas, haut = float(bar["low"]), float(bar["high"])

        # 1) Nourrir les indicateurs avec la barre qui vient de CLÔTURER.
        #    Régime 5 m aux bornes de 5 min ; l'ATR a besoin du high/low (TradeBar).
        t = bar.end_time
        if t.minute % 5 == 0:
            self.dernier_close_5m = close
            self.regime.update(t, close)
        self.rsi.update(t, close)
        self.macd.update(t, close)
        tb = TradeBar(bar.time, self.btc, float(bar["open"]), haut,
                      bas, close, float(bar["volume"]), timedelta(minutes=1))
        self.atr.update(tb)
        if not (self.regime.is_ready and self.rsi.is_ready
                and self.macd.is_ready and self.atr.is_ready):
            return                                     # warmup : aucun signal

        atr = float(self.atr.current.value)
        regime = self._regime()
        # Croisement MACD détecté sur TOUTES les barres prêtes (même en position),
        # sinon le signe mémorisé devient obsolète.
        diff = float(self.macd.current.value) - float(self.macd.signal.current.value)
        croise_haut = self.diff_prec is not None and self.diff_prec <= 0 and diff > 0
        croise_bas = self.diff_prec is not None and self.diff_prec >= 0 and diff < 0
        self.diff_prec = diff
        rsi = float(self.rsi.current.value)
        pos = self.portfolio[self.btc]
        self.barres_total += 1
        if pos.invested:
            self.barres_investi += 1

        # 2) SORTIES d'abord — le risque prime sur le signal (intra-barre, stop suiveur).
        if pos.invested:
            if pos.is_long:
                if bas <= self.stop_prix:
                    self.raison = "STOP"
                elif haut >= self.take_prix:
                    self.raison = "TAKE"
                elif regime < 0:
                    self.raison = "REGIME"
                else:                                  # rien à couper : on ratchet le stop
                    self.plus_haut = max(self.plus_haut, close)
                    self.stop_prix = max(self.stop_prix, self.plus_haut - STOP_MULT * atr)
                    return
            else:                                      # short
                if haut >= self.stop_prix:
                    self.raison = "STOP"
                elif bas <= self.take_prix:
                    self.raison = "TAKE"
                elif regime > 0:
                    self.raison = "REGIME"
                else:
                    self.plus_bas = min(self.plus_bas, close)
                    self.stop_prix = min(self.stop_prix, self.plus_bas + STOP_MULT * atr)
                    return
            self.sorties[self.raison] += 1
            self.liquidate(self.btc)
            return

        # 3) ENTRÉE : régime 5 m + le MACD croise DANS le sens du régime + RSI confirme.
        distance_stop = STOP_MULT * atr
        if distance_stop <= 0:
            return
        equite = float(self.portfolio.total_portfolio_value)
        # 4) TAILLE : risquer 0,5 % du capital ; la distance au stop fixe la quantité.
        quantite = (equite * RISQUE_PAR_TRADE) / distance_stop
        quantite = min(quantite, 0.95 * equite / close)   # garde-fou notionnel
        if quantite <= 0:
            return

        if regime > 0 and croise_haut and rsi > SEUIL_RSI:
            # LONG : stop/take posés AVANT l'ordre (fill synchrone -> on_order_event journalise).
            self.plus_haut = close
            self.stop_prix = close - distance_stop
            self.take_prix = close + TAKE_MULT * atr
            self.market_order(self.btc, quantite)
        elif regime < 0 and croise_bas and rsi < SEUIL_RSI:
            # SHORT : symétrique (stop au-dessus, take en dessous).
            self.plus_bas = close
            self.stop_prix = close + distance_stop
            self.take_prix = close - TAKE_MULT * atr
            self.market_order(self.btc, -quantite)

    def on_order_event(self, event: OrderEvent):
        if event.status != OrderStatus.FILLED:
            return
        self.nb_trades += 1
        self.frais_totaux += float(event.order_fee.value.amount)
        pos = self.portfolio[self.btc]
        if pos.invested:
            self.entry_prix = float(event.fill_price)
            sens = "LONG " if event.fill_quantity > 0 else "SHORT"
            self.log(f"TRADE {self.nb_trades:>3} {sens} {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"{float(event.fill_quantity):+.6f} BTC @ {event.fill_price} | "
                     f"stop {self.stop_prix:,.0f} / take {self.take_prix:,.0f} | "
                     f"frais={event.order_fee.value.amount:.2f} $")
        else:
            sortie = float(event.fill_price)
            if self.entry_prix:
                etait_long = event.fill_quantity < 0   # on VEND pour clôturer un long
                gain = (sortie / self.entry_prix - 1) if etait_long else (self.entry_prix / sortie - 1)
            else:
                gain = 0.0
            self.log(f"TRADE {self.nb_trades:>3} SORTIE {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"[{self.raison:<6}] @ {event.fill_price} | P&L={gain:+.2%} | "
                     f"frais={event.order_fee.value.amount:.2f} $")
            self.entry_prix = None
            self.stop_prix = None
            self.take_prix = None
            self.plus_haut = None
            self.plus_bas = None

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        exposition = self.barres_investi / self.barres_total if self.barres_total else 0.0
        self.log(f"--- BILAN STRATÉGIE AVANCÉE 1 m (régime 5 m + momentum + risque ATR), long/short ---")
        self.log(f"Trades : {self.nb_trades} | sorties : {self.sorties['STOP']} stop, "
                 f"{self.sorties['TAKE']} take, {self.sorties['REGIME']} régime | "
                 f"frais : {self.frais_totaux:.2f} $")
        self.log(f"Exposition : {exposition:.1%} des barres en position "
                 f"({self.barres_investi}/{self.barres_total})")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
