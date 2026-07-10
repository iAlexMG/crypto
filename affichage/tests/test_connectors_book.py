"""Carnet des connecteurs WS : horodatage Bybit (revue #2) + watchdog anti-gel (#8).

- Bybit v5 met `ts` (horloge exchange) a la RACINE du message, pas dans `data` -> le
  carnet doit porter ce ts, pas l'horloge locale (now_ms).
- Tous les connecteurs WS doivent suivre `_last_book` (gel du carnet detecte meme si les
  trades continuent) : porte sur bitget/okx/bybit le 2026-06-29.
"""
from __future__ import annotations

import pytest

from backend.connectors.bitget import BitgetConnector
from backend.connectors.bybit import BybitConnector
from backend.connectors.okx import OkxConnector


class FakeHub:
    def __init__(self):
        self.books = []

    def set_orderbook(self, ob):
        self.books.append(ob)

    def add_trades(self, *a):
        pass


def test_bybit_book_uses_root_timestamp():
    hub = FakeHub()
    c = BybitConnector(hub, ["BTCUSDT"])
    # message v5 realiste : `ts` a la RACINE, `data` SANS ts
    msg = {"topic": "orderbook.200.BTCUSDT", "type": "snapshot", "ts": 1700000000123,
           "data": {"s": "BTCUSDT", "b": [["60000", "1.0"]], "a": [["60001", "1.0"]], "u": 5}}
    c._handle(msg)
    assert hub.books, "aucun carnet pousse"
    assert hub.books[-1].ts == 1700000000123      # horloge EXCHANGE, pas locale


def test_bybit_book_updates_last_book():
    hub = FakeHub()
    c = BybitConnector(hub, ["BTCUSDT"])
    before = c._last_book
    c._handle({"topic": "orderbook.200.BTCUSDT", "type": "snapshot", "ts": 1700000000123,
               "data": {"s": "BTCUSDT", "b": [["60000", "1.0"]], "a": [["60001", "1.0"]], "u": 5}})
    assert c._last_book > before                   # carnet vivant -> watchdog arme


@pytest.mark.parametrize("cls", [BitgetConnector, BybitConnector, OkxConnector])
def test_connectors_track_last_book(cls):
    """Watchdog anti-gel : les 3 connecteurs portes ont l'attribut _last_book."""
    c = cls(FakeHub(), ["BTCUSDT"])
    assert hasattr(c, "_last_book")
    assert c._last_book == 0.0                      # initialise, arme a la (re)connexion
