# Expérience anatomie (bis) : sémantique du fill EN SÉANCE (résolution minute).
from AlgorithmImports import *


class AnatomieMinute(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2013, 10, 7)
        self.set_end_date(2013, 10, 8)
        self.set_cash(100_000)
        self.spy = self.add_equity("SPY", Resolution.MINUTE).symbol
        self.done = False

    def on_data(self, data: Slice):
        if self.spy not in data.bars:
            return
        bar = data.bars[self.spy]
        t = self.time
        if t.day == 7 and t.hour == 10 and t.minute <= 2 or (t.day == 7 and t.hour == 9 and t.minute == 59):
            self.log(f"barre {bar.time:%H:%M:%S} -> {bar.end_time:%H:%M:%S} "
                     f"O={bar.open} H={bar.high} L={bar.low} C={bar.close}")
        if not self.done and t.day == 7 and t.hour == 10 and t.minute == 0:
            self.market_order(self.spy, 100)
            self.done = True

    def on_order_event(self, event: OrderEvent):
        self.log(f"ORDRE -> {event}")
