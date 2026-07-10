"""Client REST KuCoin (Spot + Futures) -- LIMITE par l'API publique.

Comme Bybit, KuCoin n'expose PAS d'historique profond des trades :
- Spot    : `GET /api/v1/market/histories?symbol=BTC-USDT`  -> ~100 trades RECENTS
- Futures : `GET /api/v1/trade/history?symbol=XBTUSDTM`      -> ~100 trades RECENTS
Aucune pagination par temps ni par id. Consequences pour le collecteur :
- pas de backfill profond (PHASE B inoperante), pas de comblage de trou PROFOND ;
- on fournit neanmoins les trades RECENTS -> combler un petit trou tout frais.

L'historique KuCoin grandit donc VERS L'AVANT (comme Bybit). Pas de `fetch_range`
(seek par temps impossible) -> le service renvoie None. Timestamps en NANOSECONDES
(to_ms) ; tailles Futures en CONTRATS (x multiplier). `side` = taker = agresseur.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from ..models import Trade
from .kucoin import (CONTRACT_MULT, FUT_HOST, SPOT_HOST, market_symbol, to_ms)

log = logging.getLogger("connector.kucoin.rest")

_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}


def _get(host: str, path: str, params: dict) -> list[dict]:
    url = f"{host}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        payload = json.load(r)
    if str(payload.get("code")) != "200000":
        raise RuntimeError(f"kucoin rest {payload.get('code')}: {payload.get('msg')}")
    return payload.get("data") or []


def _recent(market: str, symbol: str, name: str) -> list[Trade]:
    """Trades RECENTS, normalises et tries par ts croissant. Spot et Futures ont des
    endpoints et des noms de champs differents (price/size identiques, ts vs time)."""
    ksym = market_symbol(market, symbol)
    if market == "FUTURES":
        rows = _get(FUT_HOST, "/api/v1/trade/history", {"symbol": ksym})
        mult = CONTRACT_MULT.get(ksym, 1.0)
        ts_key = "ts"
    else:
        rows = _get(SPOT_HOST, "/api/v1/market/histories", {"symbol": ksym})
        mult = 1.0
        ts_key = "time"
    out = [
        Trade(
            exchange=name,
            symbol=symbol,
            price=float(d["price"]),
            size=float(d["size"]) * mult,
            side="buy" if d.get("side") == "buy" else "sell",
            ts=to_ms(d.get(ts_key)),
            trade_id=str(d.get("tradeId", "")),
        )
        for d in rows
    ]
    out.sort(key=lambda t: t.ts)
    return out


def fetch_since(market: str, symbol: str, start_ms: int,
                name: str = "kucoin") -> list[Trade]:
    """Trades a partir de start_ms (RECENTS uniquement -> tronque a ce que l'API rend)."""
    return [t for t in _recent(market, symbol, name) if t.ts >= start_ms]


def fetch_before(market: str, symbol: str, before_id: str | None = None,
                 name: str = "kucoin") -> list[Trade]:
    """Interface symetrique des autres exchanges. KuCoin n'ayant PAS de pagination, on
    renvoie le paquet RECENT : le collecteur ne garde que les trades DANS le trou (filtre
    par ts) et s'arrete quand une passe ne progresse plus. Trou PROFOND = non comblable
    (pas de data REST), comme Bybit."""
    return _recent(market, symbol, name)
