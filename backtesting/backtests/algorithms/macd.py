# MACD : momentum par convergence/divergence de deux EMA sur BTCUSDT.
# Retour au SUIVI DE TENDANCE (comme sma_croisement.py), mais plus réactif :
#   MACD = EMA(12) - EMA(26)          -> le momentum (vitesse de la tendance)
#   signal = EMA(9) du MACD           -> lissage du MACD
#   histogramme = MACD - signal       -> change de signe AU croisement
# Achat quand le MACD passe AU-DESSUS de son signal (histogramme > 0),
# vente quand il repasse en-dessous. Long/flat -> comparable aux autres stratégies du cours.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
RAPIDE, LENTE, SIGNAL = 12, 26, 9      # réglages classiques du MACD


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

        # ── NOUVEAU : le MACD, nourri à la main. On mémorise le signe précédent de
        # l'histogramme (MACD - signal) pour détecter le CROISEMENT (comme diff_prec
        # dans sma_croisement.py, mais entre le MACD et sa propre ligne de signal).
        self.macd = MovingAverageConvergenceDivergence(RAPIDE, LENTE, SIGNAL,
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
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>2} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"MACD={self.macd.current.value:+.1f} signal={self.macd.signal.current.value:+.1f} | "
                     f"qté={event.fill_quantity:+.8f} @ {event.fill_price} | "
                     f"frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN MACD ({RAPIDE},{LENTE},{SIGNAL}) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
