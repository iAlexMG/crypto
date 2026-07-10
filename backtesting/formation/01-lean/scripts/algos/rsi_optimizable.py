# RSI (retour à la moyenne) — version PARAMÉTRABLE pour l'optimisation.
# Clone exact de backtests/algorithms/rsi_retour_moyenne.py, à une chose près : la
# période et le seuil sont lus via get_parameter. Deux paramètres -> heatmap 2D.
#   rsi_period (int) : période du RSI (lissage de Wilder)
#   seuil      (int) : niveau de survente -> ACHAT ; le surachat -> VENTE est SYMÉTRIQUE
#                      (surachat = 100 - seuil). seuil=30 => bandes 30/70 (défaut formation).
# En (14, 30) SANS --parameters, reproduit le RsiRetourMoyenne de la formation au chiffre près.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_RSI_DEFAUT = 14
SEUIL_DEFAUT = 30          # survente 30 -> surachat 70 (symétrique)


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


class RsiOptimizable(QCAlgorithm):

    def initialize(self):
        # ── Réglages lus dans les paramètres LEAN ; surachat symétrique du seuil.
        self.periode_rsi = int(self.get_parameter("rsi_period", PERIODE_RSI_DEFAUT))
        self.survente = int(self.get_parameter("seuil", SEUIL_DEFAUT))
        self.surachat = 100 - self.survente

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

        # RSI (Wilder) nourri à la main ; on mémorise la valeur précédente pour détecter
        # le FRANCHISSEMENT d'un seuil (comme rsi_prec dans la formation).
        self.rsi = RelativeStrengthIndex(self.periode_rsi, MovingAverageType.WILDERS)
        self.rsi_prec = None

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

        self.rsi.update(data[self.btc].end_time, close)
        if not self.rsi.is_ready:
            return

        rsi = float(self.rsi.current.value)
        if self.rsi_prec is not None:
            entre_survente = self.rsi_prec >= self.survente and rsi < self.survente
            entre_surachat = self.rsi_prec <= self.surachat and rsi > self.surachat
            if entre_survente and not self.portfolio[self.btc].invested:
                self.set_holdings(self.btc, 1.0)
            elif entre_surachat and self.portfolio[self.btc].invested:
                self.liquidate(self.btc)
        self.rsi_prec = rsi

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN RSI {self.periode_rsi} ({self.survente}/{self.surachat}) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
