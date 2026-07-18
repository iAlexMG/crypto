# RSI : retour à la moyenne — SCALPING 1 m, multi-TF, long/short.
# Contre-tendance FILTRÉE : on ne fade QUE dans le sens du régime 5 m
#   - régime haussier + survente 1 m  -> LONG  (acheter le repli d'une tendance haussière)
#   - régime baissier + surachat 1 m  -> SHORT (vendre le rebond d'une tendance baissière)
# Sortie = retour à la moyenne (RSI ~50) OU take (~0,4 %) OU stop (~0,5 %). Long ET short.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_RSI = 9           # RSI plus court qu'en horaire (14) -> plus réactif en 1 m
SURVENTE = 25            # RSI < 25 = survente
SURACHAT = 75            # RSI > 75 = surachat
MOYENNE = 50            # retour à la moyenne = sortie
REGIME_5M = 50          # SMA de régime sur barres 5 m (≈ 4 h)
STOP_PCT = 0.005        # stop 0,5 %
TAKE_PCT = 0.004        # cible 0,4 % (horizon modéré)


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

        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
        self.rsi_prec = None
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

        self.rsi.update(bar.end_time, close)
        if not self.rsi.is_ready:
            return
        rsi = float(self.rsi.current.value)
        pos = self.portfolio[self.btc]
        bas, haut = float(bar["low"]), float(bar["high"])

        if pos.invested and self.prix_entree is not None:
            # ── EN POSITION : gérer les sorties (stop / take / retour à la moyenne)
            e = self.prix_entree
            if pos.is_long:
                if bas <= e * (1 - STOP_PCT) or haut >= e * (1 + TAKE_PCT) or rsi >= MOYENNE:
                    self.liquidate(self.btc); self.prix_entree = None
            elif pos.is_short:
                if haut >= e * (1 + STOP_PCT) or bas <= e * (1 - TAKE_PCT) or rsi <= MOYENNE:
                    self.liquidate(self.btc); self.prix_entree = None
        elif self.rsi_prec is not None:
            # ── À PLAT : chercher une entrée (fade dans le sens du régime 5 m)
            entre_survente = self.rsi_prec >= SURVENTE and rsi < SURVENTE
            entre_surachat = self.rsi_prec <= SURACHAT and rsi > SURACHAT
            regime = self._regime()
            if entre_survente and regime > 0:
                self.set_holdings(self.btc, 1.0)     # long : repli en tendance haussière
            elif entre_surachat and regime < 0:
                self.set_holdings(self.btc, -1.0)    # short : rebond en tendance baissière
        self.rsi_prec = rsi

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            if self.portfolio[self.btc].invested:
                self.prix_entree = float(event.fill_price)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>3} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"RSI={self.rsi.current.value:.1f} | qté={event.fill_quantity:+.8f} @ "
                     f"{event.fill_price} | frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        self.log(f"--- BILAN RSI {PERIODE_RSI} ({SURVENTE}/{SURACHAT}) 1 m, régime 5 m, long/short ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
