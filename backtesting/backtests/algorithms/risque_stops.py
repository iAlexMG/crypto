# Gestion du risque — SCALPING 1 m, multi-TF, long/short.
# MÊME signal que rsi_retour_moyenne.py, mais on SÉPARE la logique de signal (RSI)
# de la logique de risque : stops DIMENSIONNÉS À LA VOLATILITÉ (ATR).
# RE-CALIBRAGE anti-frais (2026-07-17) : la v1 (ATR 1 m) sur-tradait (940 ordres, 32 k$).
# Un ATR 1 m est minuscule -> stops tissus -> churn. Leviers :
#   - SIGNAL RSI + ATR sur barres 5 m AGRÉGÉES (l'ATR a besoin de vraies barres OHLC :
#     open/high/low/close reconstruits sur la fenêtre de 5 min) ; stop/take en 1 m.
#   - stop = entrée ∓ ATR5m × 2 | take = entrée ± ATR5m × 3 (R:R ≈ 1,5).
#   - COOLDOWN 45 min après sortie.
# Sortie possible : STOP · TAKE · SIGNAL (RSI revenu à la moyenne). Long ET short.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_RSI = 9           # RSI court (sur barres 3 m ≈ 27 min)
SURVENTE = 30            # RSI < 30 = survente (desserré : la v2 25/75 sous-tradait)
SURACHAT = 70            # RSI > 70 = surachat
MOYENNE = 50            # RSI revenu à ~50 = fin du retour à la moyenne (sortie SIGNAL)
TF_SIGNAL = 3           # cadence du signal (minutes) : RSI + ATR sur barres 3 m
REGIME_N = 50           # SMA de régime sur barres 3 m (≈ 2,5 h)
PERIODE_ATR = 14          # ATR (barres 3 m) pour dimensionner les stops à la volatilité
STOP_MULT = 2.0           # stop  = entrée ∓ ATR3m × 2,0
TAKE_MULT = 3.0           # take  = entrée ± ATR3m × 3,0  (R:R ≈ 1,5)
COOLDOWN_MIN = 45         # pas de nouvelle entrée dans les 45 min après une sortie


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

        # Le SIGNAL : RSI + filtre de régime, sur barres 5 m.
        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
        self.rsi_prec = None
        self.sma_regime = SimpleMovingAverage(REGIME_N)
        self.dernier_close_sig = None
        # Le RISQUE : ATR sur barres 5 m agrégées + niveaux stop/take posés à l'entrée.
        self.atr = AverageTrueRange(PERIODE_ATR, MovingAverageType.WILDERS)
        self.o5 = self.h5 = self.l5 = None    # accumulateur OHLC de la fenêtre 5 min
        self.prix_entree = None
        self.stop_prix = None
        self.take_prix = None
        self.temps_sortie = None
        self.raison = ""

        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.nb_stop = 0
        self.nb_take = 0
        self.nb_signal = 0
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

        # Accumuler la barre 5 m (open du 1er 1 m, high/low étendus).
        if self.o5 is None:
            self.o5, self.h5, self.l5 = float(bar["open"]), haut, bas
        else:
            self.h5 = max(self.h5, haut)
            self.l5 = min(self.l5, bas)

        # 1) EN POSITION : le RISQUE prioritaire (stop/take ATR, intra-barre, chaque minute).
        pos = self.portfolio[self.btc]
        if pos.invested and self.prix_entree is not None:
            if pos.is_long:
                if bas <= self.stop_prix:
                    self.raison = "STOP  "; self.nb_stop += 1
                    self.liquidate(self.btc); self.temps_sortie = t
                elif haut >= self.take_prix:
                    self.raison = "TAKE  "; self.nb_take += 1
                    self.liquidate(self.btc); self.temps_sortie = t
            elif pos.is_short:
                if haut >= self.stop_prix:
                    self.raison = "STOP  "; self.nb_stop += 1
                    self.liquidate(self.btc); self.temps_sortie = t
                elif bas <= self.take_prix:
                    self.raison = "TAKE  "; self.nb_take += 1
                    self.liquidate(self.btc); self.temps_sortie = t

        # 2) SIGNAL : RSI + ATR (barre 5 m) + régime, aux bornes de 5 min.
        if t.minute % TF_SIGNAL != 0:
            return
        tb5 = TradeBar(t, self.btc, self.o5, self.h5, self.l5, close, 0.0, timedelta(minutes=TF_SIGNAL))
        self.atr.update(tb5)
        self.rsi.update(t, close)
        self.sma_regime.update(t, close)
        self.dernier_close_sig = close
        self.o5 = self.h5 = self.l5 = None     # reset de l'accumulateur 5 m
        if not (self.rsi.is_ready and self.atr.is_ready):
            return
        rsi = float(self.rsi.current.value)
        pos = self.portfolio[self.btc]          # relire (le risque a pu liquider)

        if pos.invested and self.prix_entree is not None:
            # sortie SIGNAL : RSI revenu à la moyenne
            if (pos.is_long and rsi >= MOYENNE) or (pos.is_short and rsi <= MOYENNE):
                self.raison = "SIGNAL"; self.nb_signal += 1
                self.liquidate(self.btc); self.temps_sortie = t
        elif self.rsi_prec is not None and self._cooldown_ok(t):
            entre_survente = self.rsi_prec >= SURVENTE and rsi < SURVENTE
            entre_surachat = self.rsi_prec <= SURACHAT and rsi > SURACHAT
            regime = self._regime()
            if entre_survente and regime > 0:
                self.set_holdings(self.btc, 1.0)     # long : repli en tendance haussière
            elif entre_surachat and regime < 0:
                self.set_holdings(self.btc, -1.0)    # short : rebond en tendance baissière
        self.rsi_prec = rsi

    def on_order_event(self, event: OrderEvent):
        if event.status != OrderStatus.FILLED:
            return
        self.nb_trades += 1
        self.frais_totaux += float(event.order_fee.value.amount)
        pos = self.portfolio[self.btc]
        if pos.invested:
            # Entrée : on pose les niveaux stop/take à partir de l'ATR 5 m courant.
            e = float(event.fill_price)
            self.prix_entree = e
            atr = float(self.atr.current.value)
            if pos.is_long:
                self.stop_prix = e - STOP_MULT * atr
                self.take_prix = e + TAKE_MULT * atr
            else:
                self.stop_prix = e + STOP_MULT * atr
                self.take_prix = e - TAKE_MULT * atr
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>3} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"RSI={self.rsi.current.value:.1f} ATR={atr:.1f} | @ {event.fill_price} | "
                     f"stop={self.stop_prix:.1f} take={self.take_prix:.1f} | "
                     f"frais={event.order_fee.value.amount:.2f} $")
        else:
            sortie = float(event.fill_price)
            if self.prix_entree:
                etait_long = event.fill_quantity < 0    # on VEND pour clôturer un long
                gain = (sortie / self.prix_entree - 1) if etait_long else (self.prix_entree / sortie - 1)
            else:
                gain = 0.0
            self.log(f"TRADE {self.nb_trades:>3} SORTIE {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"[{self.raison}] @ {event.fill_price} | P&L={gain:+.2%} | "
                     f"frais={event.order_fee.value.amount:.2f} $")
            self.prix_entree = self.stop_prix = self.take_prix = None

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        self.log(f"--- BILAN RSI {PERIODE_RSI} + RISQUE ATR{PERIODE_ATR} (5 m, stop ×{STOP_MULT} / "
                 f"take ×{TAKE_MULT}), régime 4 h, long/short, cooldown {COOLDOWN_MIN}m ---")
        self.log(f"Trades : {self.nb_trades} | sorties : {self.nb_stop} stop, "
                 f"{self.nb_take} take, {self.nb_signal} signal | frais : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
