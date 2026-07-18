# Gestion du risque — SCALPING 1 m, multi-TF, long/short.
# MÊME signal que rsi_retour_moyenne.py, mais on SÉPARE la logique de signal (RSI)
# de la logique de risque. La leçon reste la même qu'en horaire : couper le trade
# catastrophe AVANT qu'il ne saigne (le fameux achat tenu jusqu'à -27 000 $).
# Ce qui change en 1 m : les stops en % FIXE (8 %/10 %) n'ont plus de sens à cette
# échelle. On les remplace par des stops DIMENSIONNÉS À LA VOLATILITÉ (ATR 14) :
#   stop = entrée ∓ ATR×1   |   take = entrée ± ATR×1,5   (R:R ≈ 1,5).
# Sortie possible : STOP (risque) · TAKE (risque) · SIGNAL (RSI revenu à la moyenne).
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_RSI = 9           # RSI court -> plus réactif en 1 m
SURVENTE = 25            # RSI < 25 = survente
SURACHAT = 75            # RSI > 75 = surachat
MOYENNE = 50            # RSI revenu à ~50 = fin du retour à la moyenne (sortie SIGNAL)
REGIME_5M = 50          # SMA de régime sur barres 5 m (≈ 4 h)
PERIODE_ATR = 14          # ATR pour dimensionner les stops à la volatilité
STOP_MULT = 1.0           # stop  = entrée ∓ ATR × 1,0
TAKE_MULT = 1.5           # take  = entrée ± ATR × 1,5  (R:R ≈ 1,5)


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

        # Le SIGNAL : identique à rsi_retour_moyenne.py (RSI + filtre de régime 5 m).
        self.rsi = RelativeStrengthIndex(PERIODE_RSI, MovingAverageType.WILDERS)
        self.rsi_prec = None
        self.sma_regime = SimpleMovingAverage(REGIME_5M)
        self.dernier_close_5m = None
        # Le RISQUE : ATR + niveaux stop/take posés à l'entrée + raison de la sortie.
        self.atr = AverageTrueRange(PERIODE_ATR, MovingAverageType.WILDERS)
        self.prix_entree = None
        self.stop_prix = None
        self.take_prix = None
        self.raison = ""

        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.nb_stop = 0
        self.nb_take = 0
        self.nb_signal = 0
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

        # Régime 5 m : SMA nourrie du close aux bornes de 5 min (consolidateur manuel).
        if bar.end_time.minute % 5 == 0:
            self.dernier_close_5m = close
            self.sma_regime.update(bar.end_time, close)

        # Nourrir les indicateurs avec la barre qui vient de clôturer.
        # L'ATR a besoin du high/low -> on lui reconstruit une TradeBar complète.
        self.rsi.update(bar.end_time, close)
        tb = TradeBar(bar.time, self.btc, float(bar["open"]), float(bar["high"]),
                      float(bar["low"]), close, float(bar["volume"]), timedelta(minutes=1))
        self.atr.update(tb)
        if not (self.rsi.is_ready and self.atr.is_ready):
            return
        rsi = float(self.rsi.current.value)
        pos = self.portfolio[self.btc]
        bas, haut = float(bar["low"]), float(bar["high"])

        if pos.invested and self.prix_entree is not None:
            # ── EN POSITION : le RISQUE est prioritaire (stop/take ATR, intra-barre) ──
            if pos.is_long:
                if bas <= self.stop_prix:
                    self.raison = "STOP  "; self.nb_stop += 1; self.liquidate(self.btc)
                elif haut >= self.take_prix:
                    self.raison = "TAKE  "; self.nb_take += 1; self.liquidate(self.btc)
                elif rsi >= MOYENNE:
                    self.raison = "SIGNAL"; self.nb_signal += 1; self.liquidate(self.btc)
            elif pos.is_short:
                if haut >= self.stop_prix:
                    self.raison = "STOP  "; self.nb_stop += 1; self.liquidate(self.btc)
                elif bas <= self.take_prix:
                    self.raison = "TAKE  "; self.nb_take += 1; self.liquidate(self.btc)
                elif rsi <= MOYENNE:
                    self.raison = "SIGNAL"; self.nb_signal += 1; self.liquidate(self.btc)
        elif self.rsi_prec is not None:
            # ── À PLAT : chercher une entrée (fade dans le sens du régime 5 m) ──
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
            # Entrée : on pose les niveaux stop/take à partir de l'ATR courant.
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
            # Sortie : P&L selon le sens (long: sortie/entrée ; short: entrée/sortie).
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
        self.log(f"--- BILAN RSI {PERIODE_RSI} + RISQUE ATR{PERIODE_ATR} "
                 f"(stop ×{STOP_MULT} / take ×{TAKE_MULT}) 1 m, régime 5 m, long/short ---")
        self.log(f"Trades : {self.nb_trades} | sorties : {self.nb_stop} stop, "
                 f"{self.nb_take} take, {self.nb_signal} signal | frais : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
