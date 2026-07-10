"""Parite de normalisation OKX live vs REST (revue critique #1).

Le bug n°1 (trades OKX-swap REST stockes en CONTRATS bruts, 100x/10x trop gros) serait
passe au filet avec ce test trivial : on verifie que le REST applique le meme ctVal que
le live (okx._mult). C'est exactement le genre de test absent qui a laisse passer le bug.
"""
from __future__ import annotations

from backend.connectors import okx_rest
from backend.connectors.okx import CONTRACT_VAL


def _row(sz="5", tid="42"):
    return {"px": "60000", "sz": sz, "side": "buy", "ts": "1700000000000", "tradeId": tid}


def test_futures_btc_scaled_by_ctval():
    mult = okx_rest._mult_for("FUTURES", "BTCUSDT")
    assert mult == CONTRACT_VAL["BTCUSDT"] == 0.01
    t = okx_rest._to_trade("okx", "BTCUSDT", _row("5"), mult)
    assert t.size == 0.05            # 5 contrats x 0.01 BTC


def test_futures_eth_scaled_by_ctval():
    mult = okx_rest._mult_for("FUTURES", "ETHUSDT")
    assert mult == CONTRACT_VAL["ETHUSDT"] == 0.1
    t = okx_rest._to_trade("okx", "ETHUSDT", _row("5"), mult)
    assert t.size == 0.5             # 5 contrats x 0.1 ETH


def test_spot_not_scaled():
    mult = okx_rest._mult_for("SPOT", "BTCUSDT")
    assert mult == 1.0
    t = okx_rest._to_trade("okx", "BTCUSDT", _row("5"), mult)
    assert t.size == 5.0             # spot = deja en actif de base


def test_fetch_before_applies_scaling(monkeypatch):
    """Le chemin complet (fetch_before -> _to_trade) doit scaler, pas seulement le helper."""
    page = [_row("3", "100"), _row("7", "101")]
    calls = {"n": 0}

    def fake_fetch_page(market, symbol, after=None, limit=100):
        calls["n"] += 1
        return page if calls["n"] == 1 else []   # une seule page puis fin

    monkeypatch.setattr(okx_rest, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(okx_rest.time, "sleep", lambda *_: None)
    trades = okx_rest.fetch_before("FUTURES", "BTCUSDT")
    # 3 et 7 contrats -> 0.03 et 0.07 BTC (×0.01), peu importe l'ordre
    assert sorted(t.size for t in trades) == [0.03, 0.07]


def test_fetch_since_applies_scaling(monkeypatch):
    page = [{"px": "60000", "sz": "4", "side": "sell", "ts": "1700000005000", "tradeId": "7"}]
    calls = {"n": 0}

    def fake_fetch_page(market, symbol, after=None, limit=100):
        calls["n"] += 1
        return page if calls["n"] == 1 else []

    monkeypatch.setattr(okx_rest, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(okx_rest.time, "sleep", lambda *_: None)
    trades = okx_rest.fetch_since("FUTURES", "BTCUSDT", start_ms=0)
    assert [t.size for t in trades] == [0.04]    # 4 contrats x 0.01
