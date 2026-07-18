# Croisement de moyennes mobiles (SMA 50/200) sur BTCUSDT.
# Première VRAIE stratégie : des signaux -> des ordres -> un journal de trades,
# puis face-à-face avec le Buy & Hold (buyhold.py).
#   Règle : long/flat (jamais de vente à découvert) -> comparable au B&H.
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Mêmes constantes / même source de vérité que donnees.py et buyhold.py
DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004      # 0,04 % Binance USDⓈ-M — LA constante de frais de la formation
CAPITAL = 100_000
PERIODE_RAPIDE = 50       # SMA courte (50 h ≈ 2 jours)
PERIODE_LENTE = 200       # SMA longue (200 h ≈ 8 jours) — le « golden cross » horaire


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


class SmaCroisement(QCAlgorithm):

    def initialize(self):
        # Bornes adaptatives : lues dans le CSV (jamais de dates figées)
        with open(DATA_FILE) as f:
            rows = f.read().splitlines()
        premier = datetime.strptime(rows[1][:19], "%Y-%m-%d %H:%M:%S")
        dernier = datetime.strptime(rows[-1][:19], "%Y-%m-%d %H:%M:%S")
        self.set_start_date(premier.year, premier.month, premier.day)
        self.set_end_date(dernier.year, dernier.month, dernier.day)
        self.set_cash(CAPITAL)
        self.set_time_zone(TimeZones.UTC)

        # SymbolProperties + heures 24/7 : identiques à buyhold.py
        proprietes = SymbolProperties("BTCUSDT perpetuel USDS-M", "USD", 1,
                                      0.1, 0.00000001, "BTCUSDT")
        heures = SecurityExchangeHours.always_open(TimeZones.UTC)
        securite = self.add_data(BtcUsdt1m, "BTCUSDT", proprietes, heures,
                                 Resolution.MINUTE)
        securite.set_fee_model(FraisTakerBinance())
        self.btc = securite.symbol

        # ── NOUVEAU : deux moyennes mobiles, mises à jour À LA MAIN dans on_data.
        # (Choix pédagogique : plus transparent que le câblage automatique, et ça
        #  montre exactement AVEC QUELLE donnée l'indicateur se nourrit -> le close
        #  d'une barre CLÔTURÉE, donc aucun regard vers le futur.)
        self.sma_rapide = SimpleMovingAverage(PERIODE_RAPIDE)
        self.sma_lente = SimpleMovingAverage(PERIODE_LENTE)
        self.diff_prec = None      # signe précédent de (rapide - lente), pour détecter le croisement

        # Journal de trades + mémoire pour le face-à-face Buy & Hold
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

        # 1) Nourrir les indicateurs avec le close de la barre qui vient de CLÔTURER
        self.sma_rapide.update(data[self.btc].end_time, close)
        self.sma_lente.update(data[self.btc].end_time, close)
        if not (self.sma_rapide.is_ready and self.sma_lente.is_ready):
            return   # warmup : pas encore 200 barres -> aucun signal

        # 2) Détecter le croisement (changement de signe de rapide - lente)
        diff = self.sma_rapide.current.value - self.sma_lente.current.value
        if self.diff_prec is not None:
            croise_haut = self.diff_prec <= 0 and diff > 0   # rapide passe AU-DESSUS
            croise_bas = self.diff_prec >= 0 and diff < 0    # rapide passe EN-DESSOUS
            if croise_haut and not self.portfolio[self.btc].invested:
                self.set_holdings(self.btc, 1.0)             # entrée : tout en BTC
            elif croise_bas and self.portfolio[self.btc].invested:
                self.liquidate(self.btc)                     # sortie : 100 % cash
        self.diff_prec = diff

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>2} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"qté={event.fill_quantity:+.8f} @ {event.fill_price} | "
                     f"frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1   # B&H « naïf » (close/close)
        self.log(f"--- BILAN Croisement SMA {PERIODE_RAPIDE}/{PERIODE_LENTE} ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
