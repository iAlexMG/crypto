"""Backfill : anti double-comptage REST/live (revue #4, Binance FUTURES).

Live @trade (id `t`) et REST aggTrades (id `a`) ont des espaces d'ids DISJOINTS -> la PK
ne dedoublonne pas. Le REST ne doit remplir que STRICTEMENT SOUS l'intervalle live le plus
recent ; les intervalles anterieurs sont exclus, mais les creux ENTRE eux restent comblables.
"""
from __future__ import annotations

import time

from backend.models import Trade
from gui.backfill import BackfillManager, LIVE_GAP_TOL_MS


class FakeArchive:
    def __init__(self):
        self.inserted = []

    def insert(self, tag, trades):
        self.inserted.extend(trades)


class FakeHub:
    def trades_since(self, *a):
        return []


def _trade(ts):
    return Trade("binance-futures", "BTCUSDT", 60000.0, 1.0, "buy", ts, "a" + str(ts))


def _run(iv, rest_ts):
    """Monte un backfill dont le REST renvoie `rest_ts`, applique le filtre, renvoie les ts gardes."""
    arch = FakeArchive()
    fetched = [_trade(t) for t in rest_ts]
    feeds = [("binance-futures", "binance-futures", lambda sym, start: list(fetched))]
    bm = BackfillManager(arch, FakeHub(), ["BTCUSDT"], feeds, "binance-futures")
    if iv is not None:
        bm._live_iv[("binance-futures", "BTCUSDT")] = iv
    bm._queue.append(("binance-futures", "BTCUSDT", 0))
    bm._process_one()
    return sorted(t.ts for t in arch.inserted)


def test_drops_inside_and_leading_edge():
    # live courant [1000,2000] : pre-live garde, interieur jete, course de bord (>=floor) jetee
    assert _run([[1000, 2000]], [500, 1500, 2500]) == [500]


def test_fills_reconnection_gap_between_intervals():
    # deux intervalles, creux 2000-5000, courant [5000,6000]
    # garde : 500 (pre-live) + 3500 (creux) ; jette : 1500 (couvert), 5500 (courant), 7000 (course)
    assert _run([[1000, 2000], [5000, 6000]], [500, 1500, 3500, 5500, 7000]) == [500, 3500]


def test_empty_iv_reserves_recent_zone_for_live():
    # live pas encore demarre : on remplit le passe mais on laisse les LIVE_GAP_TOL_MS recents
    now = int(time.time() * 1000)
    kept = _run(None, [now - 10_000, now - 1_000])
    assert kept == [now - 10_000]                  # now-1000 (< tol) reserve au live
