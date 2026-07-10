"""Borne du rollup : purge_rollup supprime les vieux buckets de footprint (revue #7).

Le rollup conserve le footprint bien au-dela des trades bruts (retention 7 j), mais il
doit avoir un HORIZON DUR sinon footprint_rollup/_ohlc croissent sans fin.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.archive import ROLLUP_BUCKET_MS, TradeArchive
from backend.models import Trade


@pytest.fixture
def archive(tmp_path):
    a = TradeArchive(str(tmp_path / "trades.db"))
    a._db_path = str(tmp_path / "trades.db")     # pour le comptage direct
    yield a
    a.close()


def _count(path, table):
    c = sqlite3.connect(path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


def test_purge_rollup_drops_old_keeps_recent(archive, tmp_path):
    path = str(tmp_path / "trades.db")
    old_ts = 0                              # bucket 0 (tres vieux)
    new_ts = 100 * 86_400_000              # ~100 jours -> bucket eleve
    archive.insert("USDT-FUTURES", [
        Trade("bitget", "BTCUSDT", 60000.0, 1.0, "buy", old_ts, "old"),
        Trade("bitget", "BTCUSDT", 60000.0, 1.0, "buy", new_ts, "new"),
    ])
    archive.rollup_step(1000)              # agrege les deux dans le rollup
    assert _count(path, "footprint_rollup") == 2
    assert _count(path, "footprint_ohlc") == 2

    # purge tout ce qui precede le bucket du trade recent
    removed = archive.purge_rollup(new_ts - ROLLUP_BUCKET_MS)
    assert removed == 1                    # seul le vieux bucket
    assert _count(path, "footprint_rollup") == 1   # le recent survit
    assert _count(path, "footprint_ohlc") == 1


def test_purge_rollup_noop_when_all_recent(archive, tmp_path):
    path = str(tmp_path / "trades.db")
    new_ts = 100 * 86_400_000
    archive.insert("USDT-FUTURES",
                   [Trade("bitget", "BTCUSDT", 60000.0, 1.0, "buy", new_ts, "n")])
    archive.rollup_step(1000)
    assert archive.purge_rollup(new_ts - ROLLUP_BUCKET_MS) == 0   # rien d'assez vieux
    assert _count(path, "footprint_rollup") == 1
