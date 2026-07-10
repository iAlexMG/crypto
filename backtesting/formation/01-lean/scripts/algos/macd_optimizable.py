# MACD — version PARAMÉTRABLE pour l'optimisation.
# Clone exact de backtests/algorithms/macd.py, à une chose près : les périodes des EMA
# (rapide, lente) et du signal sont lues via get_parameter. La grille ne balaie que
# (ema_fast, ema_slow) ; le signal reste à 9 (défaut) -> heatmap 2D.
# En (12,26,9) SANS --parameters, reproduit le Macd de la formation au chiffre près.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
RAPIDE_DEFAUT, LENTE_DEFAUT, SIGNAL_DEFAUT = 12, 26, 9   # réglages classiques


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


class MacdOptimizable(QCAlgorithm):

    def initialize(self):
        # ── Périodes lues dans les paramètres LEAN (défauts = réglage classique 12/26/9)
        self.rapide = int(self.get_parameter("ema_fast", RAPIDE_DEFAUT))
        self.lente = int(self.get_parameter("ema_slow", LENTE_DEFAUT))
        self.signal = int(self.get_parameter("signal", SIGNAL_DEFAUT))

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

        # MACD nourri à la main ; on mémorise le signe précédent de l'histogramme.
        self.macd = MovingAverageConvergenceDivergence(self.rapide, self.lente, self.signal,
                                                        MovingAverageType.EXPONENTIAL)
        self.hist_prec = None

        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        close = float(data[self.btc].value)
        if self.premier_close is None:
            self.premier_close = close
        self.dernier_close = close

        self.macd.update(data[self.btc].end_time, close)
        if not self.macd.is_ready:
            return

        hist = float(self.macd.current.value) - float(self.macd.signal.current.value)
        if self.hist_prec is not None:
            croise_haut = self.hist_prec <= 0 and hist > 0   # MACD passe AU-DESSUS du signal
            croise_bas = self.hist_prec >= 0 and hist < 0    # MACD passe EN-DESSOUS
            if croise_haut and not self.portfolio[self.btc].invested:
                self.set_holdings(self.btc, 1.0)
            elif croise_bas and self.portfolio[self.btc].invested:
                self.liquidate(self.btc)
        self.hist_prec = hist

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN MACD ({self.rapide},{self.lente},{self.signal}) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
