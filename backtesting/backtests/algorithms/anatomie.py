# Anatomie — notre premier algorithme, écrit à la main.
# But : voir vivre le cycle événementiel (initialize → on_data → ordre → fill).
from AlgorithmImports import *


class Anatomie(QCAlgorithm):

    def initialize(self):
        """Appelé UNE fois, avant la première donnée : on déclare le monde."""
        self.set_start_date(2013, 10, 7)    # même semaine que le backtest de conformité de l'installation
        self.set_end_date(2013, 10, 11)
        self.set_cash(100_000)
        # add_equity abonne l'algo aux barres SPY ; on garde le Symbol (l'identifiant interne)
        self.spy = self.add_equity("SPY", Resolution.DAILY).symbol
        self.bars_seen = 0

    def on_data(self, data: Slice):
        """Appelé À CHAQUE barre reçue : c'est ici qu'on vit l'événementiel."""
        if self.spy not in data.bars:       # prudence : pas de barre SPY dans ce Slice
            return
        bar = data.bars[self.spy]
        self.bars_seen += 1
        # bar.time = OUVERTURE de la barre ; bar.end_time = sa CLÔTURE (= l'heure de l'événement)
        self.log(f"barre {self.bars_seen} | {bar.time:%Y-%m-%d %H:%M} -> "
                 f"{bar.end_time:%H:%M} | O={bar.open} C={bar.close}")
        if not self.portfolio.invested:
            self.market_order(self.spy, 100)   # ordre au marché : 100 actions

    def on_order_event(self, event: OrderEvent):
        """Appelé à chaque événement d'ordre (soumis, rempli...) : notre boîte noire des fills."""
        self.log(f"ORDRE -> {event}")

    def on_end_of_algorithm(self):
        """Appelé une fois à la toute fin."""
        self.log(f"Fin : {self.bars_seen} barres vues, "
                 f"équité finale = {self.portfolio.total_portfolio_value:.2f} $")
