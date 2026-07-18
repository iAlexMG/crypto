# Bandes de Bollinger : retour à la moyenne ADAPTATIF — SCALPING 1 m, multi-TF, long/short.
# Comme le RSI mais avec une enveloppe qui respire avec la VOLATILITÉ (± 2σ). Fade FILTRÉ
# par le régime :
#   - régime haussier + close SOUS la bande basse  -> LONG  (repli en tendance haussière)
#   - régime baissier + close AU-DESSUS bande haute -> SHORT (rebond en tendance baissière)
# RE-CALIBRAGE anti-frais (2026-07-17) : la v1 (signal 1 m) sur-tradait (2 042 ordres,
# 57 k$ de frais). Leviers mean-reversion :
#   - BANDES sur barres 5 m (signal lu aux bornes de 5 min) ; stop/take en 1 m.
#   - COOLDOWN 45 min après sortie ; TAKE 0,8 % / STOP 1,0 % (les trades respirent).
# Sortie = retour à la bande médiane OU take OU stop. Long ET short.
from AlgorithmImports import *
from datetime import datetime, timedelta

DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000
PERIODE_BB = 20           # fenêtre de la moyenne et de l'écart-type (barres 5 m ≈ 100 min)
K_ECARTS = 2.0            # largeur des bandes = 2 écarts-types (le classique)
TF_SIGNAL = 5             # cadence du signal (minutes) : bandes lues sur barres 5 m
REGIME_N = 50             # SMA de régime sur barres 5 m (≈ 4 h)
STOP_PCT = 0.010          # stop 1,0 %
TAKE_PCT = 0.008          # cible 0,8 %
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
        self.sma_regime = SimpleMovingAverage(REGIME_N)
        self.dernier_close_sig = None
        self.mediane = None       # bande médiane courante (pour la sortie, vérifiée chaque min)

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

        # 1) EN POSITION : sorties vérifiées CHAQUE minute (stop/take intra-barre, retour médiane).
        if pos.invested and self.prix_entree is not None:
            e = self.prix_entree
            if pos.is_long:
                if (bas <= e * (1 - STOP_PCT) or haut >= e * (1 + TAKE_PCT)
                        or (self.mediane is not None and close >= self.mediane)):
                    self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t
            elif pos.is_short:
                if (haut >= e * (1 + STOP_PCT) or bas <= e * (1 - TAKE_PCT)
                        or (self.mediane is not None and close <= self.mediane)):
                    self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t

        # 2) SIGNAL : bandes + régime aux bornes de 5 min ; entrée en fade filtrée régime.
        if t.minute % TF_SIGNAL != 0:
            return
        self.dernier_close_sig = close
        self.sma_regime.update(t, close)
        self.bb.update(t, close)
        if not self.bb.is_ready:
            return
        basse = float(self.bb.lower_band.current.value)
        haute = float(self.bb.upper_band.current.value)
        self.mediane = float(self.bb.middle_band.current.value)

        pos = self.portfolio[self.btc]           # relire (une sortie a pu liquider)
        if not pos.invested and self._cooldown_ok(t):
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
        self.log(f"--- BILAN Bollinger {PERIODE_BB} / {K_ECARTS}σ (signal 5 m, régime 4 h, "
                 f"long/short, cooldown {COOLDOWN_MIN}m) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
