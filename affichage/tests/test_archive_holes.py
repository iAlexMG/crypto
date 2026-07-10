"""Archive : detection de trous + amorcage au bord, sur une VRAIE base temporaire.

Sous-tend le collecteur (PHASE A) : find_holes detecte les coupures, id_at_or_after donne
le bord superieur d'un trou (pour amorcer fetch_before pile dedans). On valide aussi que la
PK (market,symbol,trade_id) + INSERT OR IGNORE dedoublonne (invariant de l'archive).
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


def _t(ts, tid, market="USDT-FUTURES"):
    return Trade("bitget", "BTCUSDT", 60000.0, 1.0, "buy", ts, tid)


def test_find_holes_detects_gap(archive):
    # trades a 0,1,2 s puis saut de 60 s puis 62,63 s -> un seul trou (2000 -> 62000)
    trades = [_t(0, "0"), _t(1000, "1"), _t(2000, "2"), _t(62000, "62"), _t(63000, "63")]
    archive.insert("USDT-FUTURES", trades)
    holes = archive.find_holes("USDT-FUTURES", "BTCUSDT", threshold_ms=30_000)
    assert holes == [(2000, 62000)]


def test_no_hole_under_threshold(archive):
    archive.insert("USDT-FUTURES", [_t(0, "0"), _t(1000, "1"), _t(2000, "2")])
    assert archive.find_holes("USDT-FUTURES", "BTCUSDT", threshold_ms=30_000) == []


def test_id_at_or_after_is_upper_edge(archive):
    archive.insert("USDT-FUTURES", [_t(2000, "2"), _t(62000, "62"), _t(63000, "63")])
    # bord superieur du trou se terminant a 62000 = id du 1er trade a ts >= 62000
    assert archive.id_at_or_after("USDT-FUTURES", "BTCUSDT", 62000) == "62"
    assert archive.earliest("USDT-FUTURES", "BTCUSDT") == 2000
    assert archive.earliest_id("USDT-FUTURES", "BTCUSDT") == "2"


def test_insert_is_idempotent_on_pk(archive):
    """INSERT OR IGNORE sur PK(market,symbol,trade_id) -> pas de double-comptage."""
    archive.insert("USDT-FUTURES", [_t(0, "0"), _t(1000, "1")])
    archive.insert("USDT-FUTURES", [_t(1000, "1"), _t(2000, "2")])   # "1" en doublon
    rows = archive.query("USDT-FUTURES", "BTCUSDT", 0, 10_000)
    assert sorted(r.trade_id for r in rows) == ["0", "1", "2"]


def test_holes_bounded_by_since_ms(archive):
    """since_ms borne le scan : un trou DEBUTANT avant la fenetre n'est pas vu ici."""
    archive.insert("USDT-FUTURES", [_t(0, "0"), _t(62000, "62"), _t(63000, "63")])
    # fenetre depuis 62000 -> le trou (0 -> 62000) commence avant -> non detecte
    assert archive.find_holes("USDT-FUTURES", "BTCUSDT", 30_000, since_ms=62000) == []
