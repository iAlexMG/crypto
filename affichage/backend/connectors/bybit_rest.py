"""Client REST Bybit (v5) -- LIMITE par l'API publique.

Bybit n'expose PAS d'historique profond des trades : `/v5/market/recent-trade` ne
renvoie que les trades RECENTS (lineaire <=1000, spot <=60) SANS pagination par temps
ni par id (cf. docs/bybit.md). Consequences pour le collecteur :
- pas de backfill ~90j (PHASE B quasi inoperante), pas de comblage de trou PROFOND ;
- on peut neanmoins fournir les trades RECENTS -> combler un petit trou tout frais.

L'historique Bybit grandit donc VERS L'AVANT (comme le carnet/heatmap). Pas de
`fetch_range` (seek par temps impossible) -> le service renvoie None.
Pas de `fetch_depth` : le carnet s'amorce entierement en WS (snapshot/delta).

Endpoint : https://api.bybit.com/v5/market/recent-trade
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from ..models import Trade

log = logging.getLogger("connector.bybit.rest")

BASE = "https://api.bybit.com"
RECENT_PATH = "/v5/market/recent-trade"


def _limit(category: str) -> int:
    return 60 if category == "spot" else 1000      # spot plafonne a 60 cote API


def _get(path: str, params: dict):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _recent(category: str, symbol: str, name: str) -> list[Trade]:
    """Trades RECENTS, normalises et tries par ts croissant. `S` = cote agresseur."""
    data = _get(RECENT_PATH, {"category": category, "symbol": symbol,
                              "limit": str(_limit(category))})
    rows = (data.get("result") or {}).get("list") or []
    out = [
        Trade(
            exchange=name,
            symbol=symbol,
            price=float(d["price"]),
            size=float(d["size"]),
            side="buy" if d.get("side") == "Buy" else "sell",
            ts=int(d["time"]),
            trade_id=str(d.get("execId", "")),
        )
        for d in rows
    ]
    out.sort(key=lambda t: t.ts)
    return out


def fetch_since(category: str, symbol: str, start_ms: int,
                name: str = "bybit") -> list[Trade]:
    """Trades a partir de start_ms (RECENTS uniquement -> tronque a ce que l'API rend)."""
    return [t for t in _recent(category, symbol, name) if t.ts >= start_ms]


def fetch_before(category: str, symbol: str, before_id: str | None = None,
                 name: str = "bybit") -> list[Trade]:
    """Interface symetrique des autres exchanges (un paquet "plus ancien que before_id").
    Bybit n'ayant PAS de pagination, on renvoie simplement le paquet RECENT : le
    collecteur ne garde que les trades DANS le trou (filtre par ts) et s'arrete des
    qu'une passe ne progresse plus. Un trou PROFOND reste donc non comblable (pas de
    data REST) -> "settled", c'est le comportement attendu pour Bybit."""
    return _recent(category, symbol, name)
