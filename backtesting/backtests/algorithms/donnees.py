# Brancher NOS données BTCUSDT (futures perp, marge USDT) dans LEAN.
# Pas de trading ici : on vérifie le CONTRAT de données (UTC, bornes, valeurs).
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Adapte ce chemin à ta machine : la source de vérité OHLCV 1H du pipeline
DATA_FILE = "F:/data/ohlcv/BTCUSDT-um/1H.csv"


class BtcUsdtHourly(PythonData):
    """Lecteur custom : une ligne du CSV -> une barre LEAN.

    Contrat du CSV : time = OUVERTURE de la barre, ISO UTC (…+00:00) ;
    colonnes open,high,low,close,volume,buy_volume,trades.
    """

    def get_source(self, config, date, is_live):
        # LOCAL_FILE : LEAN lit notre fichier tel quel, où qu'il soit sur le disque
        return SubscriptionDataSource(DATA_FILE, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        # Ignorer l'en-tête (commence par une lettre) et les lignes vides
        if not line or not line[0].isdigit():
            return None
        cols = line.split(",")

        bar = BtcUsdtHourly()
        bar.symbol = config.symbol
        # '2026-01-01 00:00:00+00:00' -> datetime naïf ; add_data(..., TimeZones.UTC)
        # dira à LEAN de l'interpréter en UTC (PIÈGE : défaut = heure de New York !)
        t_open = datetime.strptime(cols[0][:19], "%Y-%m-%d %H:%M:%S")
        bar.time = t_open                          # ouverture de la barre
        bar.end_time = t_open + timedelta(hours=1)  # clôture = moment où on_data la reçoit
        bar.value = float(cols[4])                  # close = valeur de référence
        bar["open"] = float(cols[1])
        bar["high"] = float(cols[2])
        bar["low"] = float(cols[3])
        bar["close"] = float(cols[4])
        bar["volume"] = float(cols[5])              # en BTC
        return bar


class Donnees(QCAlgorithm):

    def initialize(self):
        # Bornes ADAPTATIVES : lues dans le CSV lui-même (l'historique s'étend avec le temps,
        # on ne fige JAMAIS de dates en dur — ni ici, ni dans la formation)
        with open(DATA_FILE) as f:
            rows = f.read().splitlines()
        premier = datetime.strptime(rows[1][:19], "%Y-%m-%d %H:%M:%S")
        dernier = datetime.strptime(rows[-1][:19], "%Y-%m-%d %H:%M:%S")
        self.set_start_date(premier.year, premier.month, premier.day)
        self.set_end_date(dernier.year, dernier.month, dernier.day)
        self.set_cash(100_000)

        # LE point critique : TOUT en UTC — l'horloge de l'algo ET la donnée custom
        self.set_time_zone(TimeZones.UTC)
        self.btc = self.add_data(BtcUsdtHourly, "BTCUSDT", Resolution.HOUR,
                                 TimeZones.UTC).symbol

        self.attendu_premier = premier   # pour le bilan de fin
        self.attendu_dernier = dernier
        self.bars = 0
        self.premiere_barre = None
        self.derniere_barre = None

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        bar = data[self.btc]
        self.bars += 1
        if self.bars <= 3:   # les 3 premières barres : inspection visuelle du contrat
            self.log(f"barre {self.bars} | {bar.time:%Y-%m-%d %H:%M} UTC -> "
                     f"{bar.end_time:%H:%M} | O={bar['open']} C={bar['close']} "
                     f"V={bar['volume']:.1f} BTC")
        if self.premiere_barre is None:
            self.premiere_barre = bar.time
        self.derniere_barre = bar.time

    def on_end_of_algorithm(self):
        ok_debut = self.premiere_barre == self.attendu_premier
        ok_fin = self.derniere_barre == self.attendu_dernier
        self.log(f"Bilan : {self.bars} barres | première {self.premiere_barre:%Y-%m-%d %H:%M} "
                 f"(= CSV ? {ok_debut}) | dernière {self.derniere_barre:%Y-%m-%d %H:%M} "
                 f"(= CSV ? {ok_fin})")
