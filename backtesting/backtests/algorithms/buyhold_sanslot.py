# Buy & Hold BTCUSDT : le premier VRAI backtest sur nos données.
# Objectif : un résultat qu'on peut recalculer À LA MAIN (test « sol de vérité »).
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Adapte ce chemin à ta machine (même source de vérité que donnees.py)
DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004      # 0,04 % Binance USDⓈ-M — LA constante de frais de toute la formation
CAPITAL = 100_000


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
    """0,04 % du notionnel, comme un ordre marché (taker) sur Binance USDⓈ-M.

    Remplace le modèle par défaut de LEAN (Interactive Brokers, minimum 1 $ —
    mesuré avec les algos d'anatomie) qui n'a aucun sens pour un perp crypto.
    """

    def get_order_fee(self, parameters):
        notionnel = abs(parameters.order.quantity) * parameters.security.price
        return OrderFee(CashAmount(notionnel * FRAIS_TAKER, "USD"))


class BuyHoldSansLot(QCAlgorithm):

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

        # SymbolProperties : le point NOUVEAU de ce backtest.
        # lot_size = 1e-8 (1 satoshi) => quantités FRACTIONNAIRES autorisées.
        # Sans ça, lot par défaut = 1 : set_holdings arrondirait à 1 BTC entier !
        proprietes = SymbolProperties(
            "BTCUSDT perpetuel USDS-M",  # description libre
            "USD",       # devise de cotation (USDT assimilé à USD — simplification assumée)
            1,           # multiplicateur de contrat
            0.1,         # tick de prix minimal (0.1 USDT sur Binance)
            0.00000001,  # taille de lot : 1 satoshi
            "BTCUSDT")   # ticker marché
        # Crypto = 24/7 : bourse toujours ouverte, EN UTC (ces heures portent aussi
        # le fuseau de la donnée -> remplace le TimeZones.UTC passé à add_data dans donnees.py)
        heures = SecurityExchangeHours.always_open(TimeZones.UTC)

        securite = self.add_data(BtcUsdt1m, "BTCUSDT", Resolution.MINUTE, TimeZones.UTC)
        securite.set_fee_model(FraisTakerBinance())
        self.btc = securite.symbol

        self.achete = False
        self.fill_prix = None
        self.frais_totaux = 0.0
        self.dernier_close = None

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        self.dernier_close = float(data[self.btc].value)   # close de la barre
        if not self.achete:
            self.achete = True
            self.set_holdings(self.btc, 1.0)   # tout le capital sur BTC, une seule fois

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.fill_prix = float(event.fill_price)
            self.frais_totaux += float(event.order_fee.value.amount)
            self.log(f"FILL {event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"qté={event.fill_quantity} BTC @ {event.fill_price} | "
                     f"frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        # ── SOL DE VÉRITÉ : on recalcule l'équité finale À LA MAIN ──
        qte = float(self.portfolio[self.btc].quantity)
        cash = float(self.portfolio.cash)
        equite_lean = float(self.portfolio.total_portfolio_value)
        equite_main = cash + qte * self.dernier_close
        rendement_btc = self.dernier_close / self.fill_prix - 1
        self.log(f"Sol de vérité : qté={qte:.8f} BTC | fill={self.fill_prix} | "
                 f"dernier close={self.dernier_close}")
        self.log(f"BTC seul : {rendement_btc:+.4%} | frais payés={self.frais_totaux:.2f} $")
        self.log(f"Équité LEAN={equite_lean:.2f} vs main={equite_main:.2f} | "
                 f"écart={equite_lean - equite_main:+.6f} $")
