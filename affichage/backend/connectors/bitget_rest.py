"""Client REST Bitget : historique des trades (Fills-History, ~90 jours).

Permet de remonter l'orderflow au-dela de la session WS courante.
- Futures : GET /api/v2/mix/market/fills-history  (productType=USDT-FUTURES)
- Spot    : GET /api/v2/spot/market/fills-history
Pagination par `idLessThan` (renvoie des trades plus ANCIENS que ce tradeId).
Endpoint public (pas d'auth pour les donnees de marche).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

from ..models import Trade

log = logging.getLogger("connector.bitget.rest")

BASE = "https://api.bitget.com"


def _endpoint(inst_type: str) -> tuple[str, dict]:
    if inst_type == "SPOT":
        return "/api/v2/spot/market/fills-history", {}
    return "/api/v2/mix/market/fills-history", {"productType": inst_type}


def _get(path: str, params: dict) -> list[dict]:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        payload = json.load(r)
    if payload.get("code") != "00000":
        raise RuntimeError(f"bitget rest {payload.get('code')}: {payload.get('msg')}")
    return payload.get("data") or []


def _to_trade(name: str, symbol: str, d: dict) -> Trade:
    return Trade(
        exchange=name,
        symbol=symbol,
        price=float(d["price"]),
        size=float(d["size"]),
        side="buy" if str(d.get("side", "")).lower() == "buy" else "sell",
        ts=int(d["ts"]),
        trade_id=str(d.get("tradeId", "")),
    )


def fetch_page(inst_type: str, symbol: str, before_id: str | None = None,
               limit: int = 1000) -> list[dict]:
    """Une page de fills (les plus recents, ou plus anciens que before_id)."""
    path, params = _endpoint(inst_type)
    params.update({"symbol": symbol, "limit": str(limit)})
    if before_id:
        params["idLessThan"] = before_id
    return _get(path, params)


def fetch_before(inst_type: str, symbol: str, before_id: str | None = None,
                 name: str = "bitget", max_pages: int = 5,
                 pause: float = 0.12) -> list[Trade]:
    """Remonte le passe : un PAQUET de trades STRICTEMENT plus anciens que
    `before_id` (None -> repart des plus recents). Pagine via `idLessThan`.
    Renvoie une liste triee par ts croissant, dedupliquee. Page vide -> on a
    atteint la limite ~90j de Fills-History (le collecteur arrete ce flux).
    Symetrique de binance_rest.fetch_before -> collecteur agnostique a l'exchange.
    """
    seen: set[str] = set()
    out: list[Trade] = []
    before: str | None = str(before_id) if before_id else None
    for _ in range(max_pages):
        page = fetch_page(inst_type, symbol, before)
        if not page:
            break
        for d in page:
            tid = str(d.get("tradeId", ""))
            if tid and tid not in seen:
                seen.add(tid)
                out.append(_to_trade(name, symbol, d))
        before = str(page[-1].get("tradeId", ""))   # page triee recent->ancien
        if not before:
            break
        time.sleep(pause)   # respect rate limit
    out.sort(key=lambda t: t.ts)
    return out


def fetch_since(inst_type: str, symbol: str, start_ms: int,
                name: str = "bitget", max_pages: int = 60,
                pause: float = 0.12) -> list[Trade]:
    """Remonte les trades jusqu'a start_ms (du plus recent au plus ancien),
    en paginant. Renvoie une liste triee par ts croissant, dedupliquee."""
    seen: set[str] = set()
    out: list[Trade] = []
    before: str | None = None
    for _ in range(max_pages):
        page = fetch_page(inst_type, symbol, before)
        if not page:
            break
        oldest_ts = int(page[-1]["ts"])
        for d in page:
            tid = str(d.get("tradeId", ""))
            if tid and tid not in seen:
                seen.add(tid)
                out.append(_to_trade(name, symbol, d))
        before = str(page[-1].get("tradeId", ""))
        if oldest_ts <= start_ms or not before:
            break
        time.sleep(pause)   # respect rate limit
    out = [t for t in out if t.ts >= start_ms]
    out.sort(key=lambda t: t.ts)
    return out
