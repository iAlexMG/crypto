# Bandes de Bollinger : retour à la moyenne ADAPTATIF sur BTCUSDT.
# La suite logique du RSI (rsi_retour_moyenne.py) : au lieu d'un seuil FIXE (RSI 30/70), une
# enveloppe qui se resserre/s'élargit avec la VOLATILITÉ. On achète sous la bande
# basse (excès de faiblesse relatif au régime courant) et on sort au RETOUR À LA
# MOYENNE (bande médiane) -> pas besoin d'attendre un surachat lointain.
#   Règle : long/flat -> comparable aux autres stratégies du cours.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_BB = 20           # fenêtre de la moyenne et de l'écart-type
K_ECARTS = 2.0            # largeur des bandes = 2 écarts-types (le classique)


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


class Bollinger(QCAlgorithm):

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

        # ── NOUVEAU : bandes de Bollinger, nourries à la main.
        # bande médiane = SMA(20) ; bandes haute/basse = médiane ± 2 écarts-types.
        # Les bandes s'ÉCARTENT quand la volatilité monte, se RESSERRENT quand elle baisse.
        self.bb = BollingerBands(PERIODE_BB, K_ECARTS, MovingAverageType.SIMPLE)

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
        # Entrée : le prix casse SOUS la bande basse (faiblesse extrême vu la volatilité)
        if not investi and close < basse:
            self.set_holdings(self.btc, 1.0)
        # Sortie : le prix est REVENU à la moyenne (bande médiane) -> objectif atteint
        elif investi and close >= mediane:
            self.liquidate(self.btc)

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            largeur = float(self.bb.band_width.current.value)
            self.log(f"TRADE {self.nb_trades:>2} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"largeur_bandes={largeur:.0f} | qté={event.fill_quantity:+.8f} @ "
                     f"{event.fill_price} | frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN Bollinger {PERIODE_BB} / {K_ECARTS}σ ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
