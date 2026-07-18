# MACD — SCALPING 1 m, multi-TF, long/short (même patron que sma_croisement.py).
#   MACD = EMA(12) - EMA(26) ; signal = EMA(9) du MACD ; croisement = changement de
#   signe de l'histogramme (MACD - signal). Plus réactif que le croisement de SMA.
#   - Entrée : croisement MACD 1 m, filtré par le régime 5 m (SMA échantillonnée à 5 min).
#   - Sortie : croisement inverse OU stop protecteur (~0,5 %). Long ET short.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
RAPIDE, LENTE, SIGNAL = 12, 26, 9      # MACD classique (option plus nerveuse : 5, 13, 9)
REGIME_5M = 50                         # SMA de régime sur barres 5 m (≈ 4 h)
STOP_PCT = 0.005                       # stop protecteur 0,5 %


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


class Macd(QCAlgorithm):

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

        # MACD 1 m (croisement via signe de l'histogramme) + régime 5 m (comme SMA)
        self.macd = MovingAverageConvergenceDivergence(RAPIDE, LENTE, SIGNAL,
                                                        MovingAverageType.EXPONENTIAL)
        self.hist_prec = None
        self.sma_regime = SimpleMovingAverage(REGIME_5M)
        self.dernier_close_5m = None

        self.prix_entree = None
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def _regime(self):
        """+1 haussier, -1 baissier, 0 pas prêt."""
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

        # Régime 5 m : échantillon aux bornes de 5 min
        if bar.end_time.minute % 5 == 0:
            self.dernier_close_5m = close
            self.sma_regime.update(bar.end_time, close)

        self.macd.update(bar.end_time, close)
        if not self.macd.is_ready:
            return

        # Stop protecteur d'abord (extrême intra-barre, fill au close)
        pos = self.portfolio[self.btc]
        if pos.invested and self.prix_entree is not None:
            bas, haut = float(bar["low"]), float(bar["high"])
            if pos.is_long and bas <= self.prix_entree * (1 - STOP_PCT):
                self.liquidate(self.btc); self.prix_entree = None
            elif pos.is_short and haut >= self.prix_entree * (1 + STOP_PCT):
                self.liquidate(self.btc); self.prix_entree = None

        # Croisement MACD (signe de l'histogramme), filtré par le régime 5 m
        hist = float(self.macd.current.value) - float(self.macd.signal.current.value)
        regime = self._regime()
        if self.hist_prec is not None:
            croise_haut = self.hist_prec <= 0 and hist > 0
            croise_bas = self.hist_prec >= 0 and hist < 0
            if croise_haut:
                if regime > 0 and not pos.is_long:
                    self.set_holdings(self.btc, 1.0)
                elif pos.is_short:
                    self.liquidate(self.btc); self.prix_entree = None
            elif croise_bas:
                if regime < 0 and not pos.is_short:
                    self.set_holdings(self.btc, -1.0)
                elif pos.is_long:
                    self.liquidate(self.btc); self.prix_entree = None
        self.hist_prec = hist

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            if self.portfolio[self.btc].invested:
                self.prix_entree = float(event.fill_price)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>3} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"qté={event.fill_quantity:+.8f} @ {event.fill_price} | "
                     f"frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        self.log(f"--- BILAN MACD ({RAPIDE},{LENTE},{SIGNAL}) 1 m, régime 5 m, long/short ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
