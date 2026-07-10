# Bandes de Bollinger — version PARAMÉTRABLE pour l'optimisation.
# Clone exact de backtests/algorithms/bollinger.py, à une chose près : la période et
# le nombre d'écarts-types sont lus via get_parameter. Deux paramètres -> heatmap 2D.
#   period_bb (int)  : fenêtre de la moyenne + écart-type
#   k_std     (float): largeur des bandes en écarts-types
# En (20, 2.0) SANS --parameters, reproduit le Bollinger de la formation au chiffre près.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_BB_DEFAUT = 20
K_ECARTS_DEFAUT = 2.0


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


class BollingerOptimizable(QCAlgorithm):

    def initialize(self):
        # ── Réglages lus dans les paramètres LEAN. k_std est un FLOAT : on lit la chaîne
        #    brute (get_parameter à 1 argument) puis on convertit — évite l'ambiguïté
        #    d'overload int qui ferait planter int.Parse("2.5").
        self.periode_bb = int(self.get_parameter("period_bb", PERIODE_BB_DEFAUT))
        raw_k = self.get_parameter("k_std")
        self.k_ecarts = float(raw_k) if raw_k else K_ECARTS_DEFAUT

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

        # Bandes de Bollinger nourries à la main (médiane = SMA, ± k écarts-types).
        self.bb = BollingerBands(self.periode_bb, self.k_ecarts, MovingAverageType.SIMPLE)

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

        self.bb.update(data[self.btc].end_time, close)
        if not self.bb.is_ready:
            return

        basse = float(self.bb.lower_band.current.value)
        mediane = float(self.bb.middle_band.current.value)
        investi = self.portfolio[self.btc].invested
        # Entrée : le prix casse SOUS la bande basse ; Sortie : retour à la médiane.
        if not investi and close < basse:
            self.set_holdings(self.btc, 1.0)
        elif investi and close >= mediane:
            self.liquidate(self.btc)

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN Bollinger {self.periode_bb} / {self.k_ecarts}σ ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
