"""Moteur de dislocation de carnet (ex-"arbitrage", revue #5).

Verifie le filtre de TAILLE (notional minimal) ajoute le 2026-06-29 : un croisement de
taille derisoire ne doit plus declencher le meme signal qu'un vrai. Plus : qty executable
= min des deux cotes, seuil net, tri par ecart decroissant.
"""
from __future__ import annotations

from backend.models import OrderBook
from gui.arbitrage import find_arbs


def _book(ex, bid, bid_sz, ask, ask_sz):
    return OrderBook(ex, "BTCUSDT", [(bid, bid_sz)], [(ask, ask_sz)], 1, True)


FEES = {"A": 1.0, "B": 1.0}     # 1 bps par cote -> 2 bps a franchir


def test_large_crossing_flagged():
    books = {"A": _book("A", 60100, 1.0, 60200, 1.0),
             "B": _book("B", 59900, 1.0, 60000, 1.0)}    # vendre A@60100, acheter B@60000
    arbs = find_arbs(books, FEES, 1.0, ts=0, min_notional=1000.0)
    assert len(arbs) == 1
    a = arbs[0]
    assert (a.sell_ex, a.buy_ex) == ("A", "B")
    assert a.qty == 1.0 and a.notional > 1000


def test_tiny_crossing_filtered():
    """Meme ecart de prix mais 0.001 BTC -> notional ~60 -> sous le seuil -> ignore."""
    books = {"A": _book("A", 60100, 0.001, 60200, 0.001),
             "B": _book("B", 59900, 0.001, 60000, 0.001)}
    assert find_arbs(books, FEES, 1.0, ts=0, min_notional=1000.0) == []


def test_no_filter_keeps_tiny():
    """Sans filtre (min_notional=0) le petit croisement repasse -> retro-compat."""
    books = {"A": _book("A", 60100, 0.001, 60200, 0.001),
             "B": _book("B", 59900, 0.001, 60000, 0.001)}
    assert len(find_arbs(books, FEES, 1.0, ts=0, min_notional=0.0)) == 1


def test_qty_is_min_of_both_sides():
    books = {"A": _book("A", 60100, 5.0, 60200, 5.0),
             "B": _book("B", 59900, 0.05, 60000, 0.05)}
    arbs = find_arbs(books, FEES, 1.0, ts=0, min_notional=1000.0)
    assert arbs and arbs[0].qty == 0.05            # borne par le plus petit cote


def test_no_crossing_no_signal():
    """Carnets disjoints (pas de croisement) -> aucune dislocation."""
    books = {"A": _book("A", 60000, 1.0, 60010, 1.0),
             "B": _book("B", 60000, 1.0, 60010, 1.0)}
    assert find_arbs(books, FEES, 1.0, ts=0, min_notional=0.0) == []


def test_threshold_respected():
    """Ecart juste sous le seuil net -> rien."""
    # edge 5$ sur mid ~60000 = ~0.83 bps brut - 2 bps fees < 0 -> sous tout seuil positif
    books = {"A": _book("A", 60002, 1.0, 60100, 1.0),
             "B": _book("B", 59900, 1.0, 59997, 1.0)}
    assert find_arbs(books, FEES, 1.0, ts=0, min_notional=0.0) == []
