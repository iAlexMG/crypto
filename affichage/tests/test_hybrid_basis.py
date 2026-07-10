"""Recalage du footprint hybride historique : basis trade-matché du live prioritaire,
repli sur les mids du rollup (revue #6 — unifier live et historique).

But : à la couture AGG_SPAN_S, le chemin historique doit pouvoir utiliser LE MÊME basis
que le live (trade-matché, figé) au lieu d'un basis mids divergent -> plus de saut de rang.
"""
from __future__ import annotations

import pytest

from backend.archive import TradeArchive
from backend.models import Trade


@pytest.fixture
def archive(tmp_path):
    a = TradeArchive(str(tmp_path / "trades.db"))
    yield a
    a.close()


def _seed(a):
    """Base @60000, feed @59990 (buy+sell des deux côtés -> mids définis), même bucket 0."""
    a.insert("USDT-FUTURES", [
        Trade("bitget", "BTCUSDT", 60000.0, 1.0, "buy", 0, "b1"),
        Trade("bitget", "BTCUSDT", 60000.0, 1.0, "sell", 1, "b2"),
    ])
    a.insert("okx-swap", [
        Trade("okx", "BTCUSDT", 59990.0, 1.0, "buy", 0, "o1"),
        Trade("okx", "BTCUSDT", 59990.0, 1.0, "sell", 1, "o2"),
    ])
    a.rollup_step(1000)


def _feed_rows(vol):
    """Rangs (drow) où le volume du feed atterrit (base_vol=False -> que le feed)."""
    return sorted({row for _cb, row, _side, _v in vol})


def test_mids_fallback_aligns_feed_to_base(archive):
    _seed(archive)
    # base_vol=False -> on ne garde que le feed recalé ; pas de basis live -> repli mids.
    vol, *_ = archive.aggregate_footprint_rollup_hybrid(
        "USDT-FUTURES", ("okx-swap",), "BTCUSDT", -1, 120_000, 60.0, 0.1,
        base_vol=False, basis_by_feed=None)
    # mid_base-mid_feed = +10 ; tick 0.1 -> +100 rangs : 599900 -> 600000 (= rang de la base)
    assert _feed_rows(vol) == [600000]


def test_live_basis_overrides_mids(archive):
    _seed(archive)
    # basis live figé = +20 (au lieu de +10 des mids) -> +200 rangs : 599900 -> 600100
    vol, *_ = archive.aggregate_footprint_rollup_hybrid(
        "USDT-FUTURES", ("okx-swap",), "BTCUSDT", -1, 120_000, 60.0, 0.1,
        base_vol=False, basis_by_feed={"okx-swap": {0: 20.0}})
    assert _feed_rows(vol) == [600100]


def test_partial_basis_falls_back_per_bucket(archive):
    _seed(archive)
    # basis fourni mais PAS pour le bucket 0 -> repli mids pour ce bucket (+10 -> 600000)
    vol, *_ = archive.aggregate_footprint_rollup_hybrid(
        "USDT-FUTURES", ("okx-swap",), "BTCUSDT", -1, 120_000, 60.0, 0.1,
        base_vol=False, basis_by_feed={"okx-swap": {999: 20.0}})
    assert _feed_rows(vol) == [600000]
