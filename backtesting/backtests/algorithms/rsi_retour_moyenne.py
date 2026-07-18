# RSI : retour à la moyenne (contre-tendance) sur BTCUSDT.
# Le MIROIR du croisement SMA : au lieu de SUIVRE la tendance (acheter la force),
# on PARIE CONTRE (acheter la faiblesse = survente, vendre la force = surachat).
#   Règle : long/flat -> directement comparable au croisement SMA et au Buy & Hold.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004      # 0,04 % Binance USDⓈ-M — LA constante de frais de la formation
CAPITAL = 100_000
PERIODE_RSI = 14          # période classique du RSI (14 barres)
SURVENTE = 30             # RSI < 30 = survente -> on ACHÈTE (pari de rebond)
SURACHAT = 70             # RSI > 70 = surachat -> on VEND (pari de repli)


class BtcUsdt1m(PythonData):
    """Lecteur custom validé (donnees.py), inchangé : une ligne du CSV -> une barre LEAN."""

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


class RsiRetourMoyenne(QCAlgorithm):

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

        # ── NOUVEAU : un RSI (oscillateur BORNÉ 0-100), nourri à la main.
        # Lissage de Wilder (le classique du RSI). On mémorise la valeur précédente
        # pour détecter le FRANCHISSEMENT d'un seuil (comme diff_prec dans sma_croisement.py).
        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
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
            # Franchissement DESCENDANT de 30 = on entre en survente -> ACHAT (rebond attendu)
            entre_survente = self.rsi_prec >= SURVENTE and rsi < SURVENTE
            # Franchissement ASCENDANT de 70 = on entre en surachat -> VENTE (repli attendu)
            entre_surachat = self.rsi_prec <= SURACHAT and rsi > SURACHAT
            if entre_survente and not self.portfolio[self.btc].invested:
                self.set_holdings(self.btc, 1.0)
            elif entre_surachat and self.portfolio[self.btc].invested:
                self.liquidate(self.btc)
        self.rsi_prec = rsi

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>2} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"RSI={self.rsi.current.value:.1f} | qté={event.fill_quantity:+.8f} @ "
                     f"{event.fill_price} | frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN RSI {PERIODE_RSI} ({SURVENTE}/{SURACHAT}) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
