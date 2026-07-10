# Croisement de moyennes mobiles SMA — version PARAMÉTRABLE pour l'optimisation.
#
# Identique à backtests/algorithms/sma_croisement.py (même lecteur de données, même
# modèle de frais, même alimentation causale des indicateurs à la CLÔTURE de la barre),
# à UNE différence près : les deux périodes ne sont plus figées en dur, elles sont lues
# via le mécanisme natif de paramètres de LEAN — `get_parameter(...)`. C'est EXACTEMENT
# ce que lit l'optimiseur natif de LEAN, donc l'orchestrateur run_grid.py peut injecter
# n'importe quelle combinaison par la ligne de commande :
#     --parameters period_fast:50,period_slow:200
#
# En (50,200) SANS --parameters, on retombe sur les défauts -> reproduit au chiffre près
# le SmaCroisement de la formation (contrôle de conformité).
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Mêmes constantes / même source de vérité que la formation
DATA_FILE = "F:/data/ohlcv/BTCUSDT-um/1H.csv"
FRAIS_TAKER = 0.0004      # 0,04 % Binance USDⓈ-M — LA constante de frais de la formation
CAPITAL = 100_000
PERIODE_RAPIDE_DEFAUT = 50    # défauts = réglage de référence de la formation (50/200)
PERIODE_LENTE_DEFAUT = 200


class BtcUsdtHourly(PythonData):
    """Lecteur custom validé (donnees.py), inchangé : une ligne du CSV -> une barre LEAN."""

    def get_source(self, config, date, is_live):
        return SubscriptionDataSource(DATA_FILE, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or not line[0].isdigit():
            return None
        cols = line.split(",")
        bar = BtcUsdtHourly()
        bar.symbol = config.symbol
        t_open = datetime.strptime(cols[0][:19], "%Y-%m-%d %H:%M:%S")
        bar.time = t_open
        bar.end_time = t_open + timedelta(hours=1)
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


class SmaOptimizable(QCAlgorithm):

    def initialize(self):
        # ── LA différence : les périodes viennent des paramètres LEAN (injectés par
        #    --parameters period_fast:X,period_slow:Y). Absents -> défauts 50/200.
        self.periode_rapide = int(self.get_parameter("period_fast", PERIODE_RAPIDE_DEFAUT))
        self.periode_lente = int(self.get_parameter("period_slow", PERIODE_LENTE_DEFAUT))

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
        securite = self.add_data(BtcUsdtHourly, "BTCUSDT", proprietes, heures,
                                 Resolution.HOUR)
        securite.set_fee_model(FraisTakerBinance())
        self.btc = securite.symbol

        # Deux moyennes mobiles, mises à jour À LA MAIN dans on_data (même choix causal
        # que la formation : l'indicateur se nourrit du close d'une barre CLÔTURÉE).
        self.sma_rapide = SimpleMovingAverage(self.periode_rapide)
        self.sma_lente = SimpleMovingAverage(self.periode_lente)
        self.diff_prec = None      # signe précédent de (rapide - lente)

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
            return   # warmup : pas encore `periode_lente` barres -> aucun signal

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

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN Croisement SMA {self.periode_rapide}/{self.periode_lente} ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement_strat - rendement_bh:+.4%}")
