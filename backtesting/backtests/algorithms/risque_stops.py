# Gestion du risque : le MÊME signal RSI que rsi_retour_moyenne.py, mais avec des
# garde-fous. On SÉPARE la logique de signal (RSI 30/70) de la logique de risque
# (stop-loss, take-profit). Objectif : couper le trade catastrophe 3->4 de
# la stratégie RSI nue (acheté 93 614, tenu jusqu'à 68 652 = -27 000 $) AVANT qu'il ne saigne.
#   Règle : long/flat -> comparable aux autres stratégies du cours.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_RSI = 14
SURVENTE = 30
SURACHAT = 70
STOP = 0.08               # stop-loss : on coupe à -8 % de l'entrée (limite la casse)
TAKE = 0.10               # take-profit : on encaisse à +10 % (sécurise le gain)


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


class RisqueStops(QCAlgorithm):

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

        # Le SIGNAL : identique à rsi_retour_moyenne.py.
        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
        self.rsi_prec = None
        # Le RISQUE : mémoire du prix d'entrée + raison de la sortie (pour le journal).
        self.entry_prix = None
        self.raison = ""

        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.nb_stop = 0
        self.nb_take = 0
        self.nb_signal = 0
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
        investi = self.portfolio[self.btc].invested

        # ── LOGIQUE DE RISQUE (prioritaire) : n'agit que si on est en position ──
        if investi and self.entry_prix is not None:
            if close <= self.entry_prix * (1 - STOP):
                self.raison = "STOP  "; self.nb_stop += 1
                self.liquidate(self.btc)
            elif close >= self.entry_prix * (1 + TAKE):
                self.raison = "TAKE  "; self.nb_take += 1
                self.liquidate(self.btc)

        # ── LOGIQUE DE SIGNAL : identique à rsi_retour_moyenne.py ──
        investi = self.portfolio[self.btc].invested   # relire (le risque a pu liquider)
        if self.rsi_prec is not None:
            entre_survente = self.rsi_prec >= SURVENTE and rsi < SURVENTE
            entre_surachat = self.rsi_prec <= SURACHAT and rsi > SURACHAT
            if entre_survente and not investi:
                self.set_holdings(self.btc, 1.0)
            elif entre_surachat and investi:
                self.raison = "SIGNAL"; self.nb_signal += 1
                self.liquidate(self.btc)
        self.rsi_prec = rsi

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            if event.fill_quantity > 0:
                self.entry_prix = float(event.fill_price)
                self.log(f"TRADE {self.nb_trades:>2} ACHAT  {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                         f"RSI={self.rsi.current.value:.1f} | @ {event.fill_price} | "
                         f"frais={event.order_fee.value.amount:.2f} $")
            else:
                gain = (float(event.fill_price) / self.entry_prix - 1) if self.entry_prix else 0.0
                self.log(f"TRADE {self.nb_trades:>2} VENTE  {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                         f"[{self.raison}] | @ {event.fill_price} | P&L={gain:+.2%} | "
                         f"frais={event.order_fee.value.amount:.2f} $")
                self.entry_prix = None

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN RSI+RISQUE (stop {STOP:.0%} / take {TAKE:.0%}) ---")
        self.log(f"Trades : {self.nb_trades} | sorties: {self.nb_stop} stop, "
                 f"{self.nb_take} take, {self.nb_signal} signal | frais : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
