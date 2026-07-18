# Bandes de Bollinger : retour à la moyenne ADAPTATIF — SCALPING 1 m, multi-TF, long/short.
# Comme le RSI mais avec une enveloppe qui respire avec la VOLATILITÉ (± 2σ). Fade FILTRÉ
# par le régime 5 m :
#   - régime haussier + close SOUS la bande basse  -> LONG  (repli en tendance haussière)
#   - régime baissier + close AU-DESSUS bande haute -> SHORT (rebond en tendance baissière)
# Sortie = retour à la bande médiane (moyenne) OU take (~0,4 %) OU stop (~0,5 %). Long ET short.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_BB = 20           # fenêtre de la moyenne et de l'écart-type
K_ECARTS = 2.0            # largeur des bandes = 2 écarts-types (le classique)
REGIME_5M = 50            # SMA de régime sur barres 5 m (≈ 4 h)
STOP_PCT = 0.005          # stop 0,5 %
TAKE_PCT = 0.004          # cible 0,4 % (horizon modéré)


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
        securite = self.add_data(BtcUsdt1m, "BTCUSDT", proprietes, heures,
                                 Resolution.MINUTE)
        securite.set_fee_model(FraisTakerBinance())
        self.btc = securite.symbol

        self.bb = BollingerBands(PERIODE_BB, K_ECARTS, MovingAverageType.SIMPLE)
        self.sma_regime = SimpleMovingAverage(REGIME_5M)
        self.dernier_close_5m = None

        self.prix_entree = None
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def _regime(self):
        if not self.sma_regime.is_ready or self.dernier_close_5m is None:
            return 0
        return 1 if self.dernier_close_5m > self.sma_regime.current.value else -1

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        bar = data[self.btc]
        close = float(bar.value)
        if self.premier_close is None:
            self.premier_close = close
        self.dernier_close = close

        if bar.end_time.minute % 5 == 0:
            self.dernier_close_5m = close
            self.sma_regime.update(bar.end_time, close)

        self.bb.update(bar.end_time, close)
        if not self.bb.is_ready:
            return

        basse = float(self.bb.lower_band.current.value)
        haute = float(self.bb.upper_band.current.value)
        mediane = float(self.bb.middle_band.current.value)
        pos = self.portfolio[self.btc]
        bas, haut = float(bar["low"]), float(bar["high"])

        if pos.invested and self.prix_entree is not None:
            # ── EN POSITION : sorties (stop / take / retour à la médiane)
            e = self.prix_entree
            if pos.is_long:
                if bas <= e * (1 - STOP_PCT) or haut >= e * (1 + TAKE_PCT) or close >= mediane:
                    self.liquidate(self.btc); self.prix_entree = None
            elif pos.is_short:
                if haut >= e * (1 + STOP_PCT) or bas <= e * (1 - TAKE_PCT) or close <= mediane:
                    self.liquidate(self.btc); self.prix_entree = None
        else:
            # ── À PLAT : entrée en fade, filtrée par le régime 5 m
            regime = self._regime()
            if close < basse and regime > 0:
                self.set_holdings(self.btc, 1.0)     # long : repli en tendance haussière
            elif close > haute and regime < 0:
                self.set_holdings(self.btc, -1.0)    # short : rebond en tendance baissière

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            if self.portfolio[self.btc].invested:
                self.prix_entree = float(event.fill_price)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            largeur = float(self.bb.band_width.current.value)
            self.log(f"TRADE {self.nb_trades:>3} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"largeur_bandes={largeur:.0f} | qté={event.fill_quantity:+.8f} @ "
                     f"{event.fill_price} | frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        self.log(f"--- BILAN Bollinger {PERIODE_BB} / {K_ECARTS}σ (1 m, régime 5 m, long/short) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
