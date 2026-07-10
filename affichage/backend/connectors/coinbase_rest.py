"""Client REST Coinbase Exchange : historique des trades (paginable, profond).

`GET /products/{id}/trades?limit=1000&after=<tradeId>` renvoie les trades les plus
RECENTS (tries du plus recent au plus ancien), ou ceux STRICTEMENT plus anciens que
`after` (pagination par tradeId decroissant, comme l'idLessThan de Bitget). Support
COMPLET du collecteur (comblage de trous + remontee du passe) via fetch_before. Pas
de seek par temps (fetch_range = None) -> l'amorcage fetch_before au bord du trou
suffit (id WS == id REST chez Coinbase, meme famille d'API).

`side` = cote du MAKER -> agresseur OPPOSE (cf. coinbase.py). Endpoint public.
Coinbase filtre l'User-Agent par defaut d'urllib -> on en envoie un explicite.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

from ..models import Trade
from .coinbase import product_id, to_ms

log = logging.getLogger("connector.coinbase.rest")

BASE = "https://api.exchange.coinbase.com"
_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}


def _get(path: str, params: dict) -> list[dict]:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _to_trade(name: str, symbol: str, d: dict) -> Trade:
    return Trade(
        exchange=name,
        symbol=symbol,
        price=float(d["price"]),
        size=float(d["size"]),
        side="buy" if d.get("side") == "sell" else "sell",   # maker -> agresseur inverse
        ts=to_ms(d.get("time")),
        trade_id=str(d.get("trade_id", "")),
    )


def fetch_page(symbol: str, after: str | None = None, limit: int = 1000) -> list[dict]:
    """Une page de trades (les plus recents, ou plus anciens que `after`).
    Triee du plus RECENT au plus ancien (comme Bitget/OKX)."""
    params: dict = {"limit": str(limit)}
    if after:
        params["after"] = after
    return _get(f"/products/{product_id(symbol)}/trades", params)


def fetch_before(symbol: str, before_id: str | None = None,
                 name: str = "coinbase", max_pages: int = 5,
                 pause: float = 0.12) -> list[Trade]:
    """Remonte le passe : un PAQUET de trades STRICTEMENT plus anciens que `before_id`
    (None -> repart des plus recents). Pagine via `after`=tradeId. Renvoie une liste
    triee par ts croissant, dedupliquee. Symetrique des autres exchanges."""
    seen: set[str] = set()
    out: list[Trade] = []
    after: str | None = str(before_id) if before_id else None
    for _ in range(max_pages):
        page = fetch_page(symbol, after)
        if not page:
            break
        for d in page:
            tid = str(d.get("trade_id", ""))
            if tid and tid not in seen:
                seen.add(tid)
                out.append(_to_trade(name, symbol, d))
        after = str(page[-1].get("trade_id", ""))    # page triee recent->ancien
        if not after:
            break
        time.sleep(pause)   # respect rate limit
    out.sort(key=lambda t: t.ts)
    return out


def fetch_since(symbol: str, start_ms: int, name: str = "coinbase",
                max_pages: int = 60, pause: float = 0.12) -> list[Trade]:
    """Remonte les trades jusqu'a start_ms (du plus recent au plus ancien), en
    paginant. Renvoie une liste triee par ts croissant, dedupliquee."""
    seen: set[str] = set()
    out: list[Trade] = []
    after: str | None = None
    for _ in range(max_pages):
        page = fetch_page(symbol, after)
        if not page:
            break
        oldest_ts = to_ms(page[-1].get("time"))
        for d in page:
            tid = str(d.get("trade_id", ""))
            if tid and tid not in seen:
                seen.add(tid)
                out.append(_to_trade(name, symbol, d))
        after = str(page[-1].get("trade_id", ""))
        if oldest_ts <= start_ms or not after:
            break
        time.sleep(pause)   # respect rate limit
    out = [t for t in out if t.ts >= start_ms]
    out.sort(key=lambda t: t.ts)
    return out
