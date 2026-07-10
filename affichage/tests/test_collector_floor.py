"""Collecteur PHASE B : pas de re-fetch REST en boucle sous le plancher Bitget (revue #3).

Quand une page REST revient ENTIEREMENT sous le plancher Bitget, l'ancien code faisait
`return False` sans avancer le curseur -> meme page re-fetchee a chaque tour (gaspillage).
Corrige : ce cas met le flux en COOLDOWN (curseur fige, on retentera quand Bitget descend).
"""
from __future__ import annotations

import time

from backend.models import Trade
from gui.collector import HistoryCollector

FLOOR = 1000


class FakeArchive:
    def __init__(self):
        self.inserted = []
        self._earliest = {}
        self._earliest_id = {}

    def earliest(self, tag, sym):
        return self._earliest.get((tag, sym))

    def earliest_id(self, tag, sym):
        return self._earliest_id.get((tag, sym))

    def insert(self, tag, trades):
        self.inserted.extend(trades)

    def find_holes(self, *a, **k):
        return []


class FakeHub:
    def last_trade_ts(self, *a):
        return 0

    def trades_since(self, *a):
        return []


def _trade(ts, tid):
    return Trade("okx", "BTCUSDT", 60000.0, 1.0, "buy", ts, tid)


def _collector(arch, fetch_before):
    arch._earliest[("USDT-FUTURES", "BTCUSDT")] = FLOOR    # plancher Bitget
    arch._earliest[("okx-swap", "BTCUSDT")] = 1010         # earliest okx > plancher
    arch._earliest_id[("okx-swap", "BTCUSDT")] = "seed"
    feeds = [("okx", "OKX", "okx-swap", fetch_before, None)]
    return HistoryCollector(arch, FakeHub(), feeds, ["BTCUSDT"],
                            base_exchange="Bitget", base_tag="USDT-FUTURES")


def test_page_below_floor_cools_and_freezes_cursor():
    arch = FakeArchive()
    fb = lambda sym, before: [_trade(900, "t900"), _trade(950, "t950")]   # tous < FLOOR
    col = _collector(arch, fb)
    fk = ("okx", "BTCUSDT")
    r = col._step_back(fk, "okx-swap", "BTCUSDT", fb)
    assert r is False
    assert arch.inserted == []                       # rien insere (tout sous plancher)
    assert col._cursor[fk] == "seed"                 # curseur FIGE (pas de re-fetch perdu)
    assert col._cooldown.get(fk, 0) > time.monotonic()   # cooldown arme


def test_mixed_page_inserts_and_advances():
    arch = FakeArchive()
    fb = lambda sym, before: [_trade(900, "t900"), _trade(1050, "t1050")]  # 900<floor, 1050>=floor
    col = _collector(arch, fb)
    fk = ("okx", "BTCUSDT")
    r = col._step_back(fk, "okx-swap", "BTCUSDT", fb)
    assert r is True
    assert [t.ts for t in arch.inserted] == [1050]   # seul le >= plancher garde
    assert col._cursor[fk] == "t1050"                # curseur avance
    assert not (col._cooldown.get(fk, 0) > time.monotonic())   # pas de cooldown en cas normal
