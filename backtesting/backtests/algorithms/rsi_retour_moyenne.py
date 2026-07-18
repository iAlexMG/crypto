# RSI : retour à la moyenne — SCALPING 1 m, multi-TF, long/short.
# Contre-tendance FILTRÉE : on ne fade QUE dans le sens du régime
#   - régime haussier + survente  -> LONG  (acheter le repli d'une tendance haussière)
#   - régime baissier + surachat  -> SHORT (vendre le rebond d'une tendance baissière)
# RE-CALIBRAGE anti-frais (2026-07-17) : la v1 (signal 1 m) sur-tradait (826 ordres,
# 27 k$ de frais). Leviers mean-reversion :
#   - RSI sur barres 5 m (signal lu aux bornes de 5 min) ; stop/take en 1 m.
#   - COOLDOWN 45 min après sortie ; TAKE 0,8 % / STOP 1,0 % (les trades respirent).
# Sortie = retour à la moyenne (RSI ~50) OU take OU stop. Long ET short.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_RSI = 9           # RSI court (sur barres 3 m ≈ 27 min)
SURVENTE = 30            # RSI < 30 = survente (desserré : la v2 25/75 sous-tradait)
SURACHAT = 70            # RSI > 70 = surachat
MOYENNE = 50            # retour à la moyenne = sortie
TF_SIGNAL = 3            # cadence du signal (minutes) : RSI lu sur barres 3 m
REGIME_N = 50           # SMA de régime sur barres 3 m (≈ 2,5 h)
STOP_PCT = 0.010        # stop 1,0 %
TAKE_PCT = 0.008        # cible 0,8 %
COOLDOWN_MIN = 45       # pas de nouvelle entrée dans les 45 min après une sortie


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
        self.sma_regime = SimpleMovingAverage(REGIME_N)
        self.dernier_close_sig = None

        self.prix_entree = None
        self.temps_sortie = None
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def _regime(self):
        if not self.sma_regime.is_ready or self.dernier_close_sig is None:
            return 0
        return 1 if self.dernier_close_sig > self.sma_regime.current.value else -1

    def _cooldown_ok(self, maintenant):
        return (self.temps_sortie is None
                or (maintenant - self.temps_sortie).total_seconds() >= COOLDOWN_MIN * 60)

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        bar = data[self.btc]
        close = float(bar.value)
        if self.premier_close is None:
            self.premier_close = close
        self.dernier_close = close
        t = bar.end_time
        bas, haut = float(bar["low"]), float(bar["high"])
        pos = self.portfolio[self.btc]

        # 1) EN POSITION : stop/take vérifiés CHAQUE minute (extrême intra-barre).
        if pos.invested and self.prix_entree is not None:
            e = self.prix_entree
            if pos.is_long and (bas <= e * (1 - STOP_PCT) or haut >= e * (1 + TAKE_PCT)):
                self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t
            elif pos.is_short and (haut >= e * (1 + STOP_PCT) or bas <= e * (1 - TAKE_PCT)):
                self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t

        # 2) SIGNAL : RSI + régime aux bornes de 5 min (retour-moyenne + entrées).
        if t.minute % TF_SIGNAL != 0:
            return
        self.dernier_close_sig = close
        self.sma_regime.update(t, close)
        self.rsi.update(t, close)
        if not self.rsi.is_ready:
            return
        rsi = float(self.rsi.current.value)
        pos = self.portfolio[self.btc]           # relire (stop/take a pu liquider)

        if pos.invested and self.prix_entree is not None:
            # sortie sur retour à la moyenne (RSI ~50)
            if pos.is_long and rsi >= MOYENNE:
                self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t
            elif pos.is_short and rsi <= MOYENNE:
                self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t
        elif self.rsi_prec is not None and self._cooldown_ok(t):
            # à plat : entrée en fade dans le sens du régime
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
        self.log(f"--- BILAN RSI {PERIODE_RSI} ({SURVENTE}/{SURACHAT}) signal 5 m, régime 4 h, "
                 f"long/short, cooldown {COOLDOWN_MIN}m ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
