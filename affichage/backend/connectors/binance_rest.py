"""Client REST Binance : snapshot d'orderbook + historique des trades (aggTrades).

Deux usages :
- `fetch_depth` : snapshot initial du carnet (lastUpdateId + niveaux), indispensable
  pour AMORCER l'orderbook (Binance = snapshot REST + updates WS raccordees par
  numero de sequence, contrairement a Bitget qui fait tout en WS).
- `fetch_since` : historique des trades agreges (aggTrades), pagination par `fromId`
  (pas de limite de fenetre 1h, contrairement a startTime+endTime). Public.

Endpoints :
- SPOT    : https://api.binance.com   (/api/v3/depth, /api/v3/aggTrades)
- FUTURES : https://fapi.binance.com  (/fapi/v1/depth, /fapi/v1/aggTrades)
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

from ..models import Trade

log = logging.getLogger("connector.binance.rest")

SPOT_BASE = "https://api.binance.com"
FUT_BASE = "https://fapi.binance.com"


def _base(market: str) -> str:
    return FUT_BASE if market == "FUTURES" else SPOT_BASE


def _depth_path(market: str) -> str:
    return "/fapi/v1/depth" if market == "FUTURES" else "/api/v3/depth"


def _aggtrades_path(market: str) -> str:
    return "/fapi/v1/aggTrades" if market == "FUTURES" else "/api/v3/aggTrades"


def _get(base: str, path: str, params: dict):
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def fetch_depth(market: str, symbol: str, limit: int = 1000) -> tuple[int, list, list]:
    """Snapshot du carnet : renvoie (lastUpdateId, bids, asks) ; bids/asks =
    listes de (price_str, qty_str). Sert a amorcer l'orderbook local."""
    data = _get(_base(market), _depth_path(market), {"symbol": symbol, "limit": str(limit)})
    last_id = int(data["lastUpdateId"])
    bids = [(p, q) for p, q in data.get("bids", [])]
    asks = [(p, q) for p, q in data.get("asks", [])]
    return last_id, bids, asks


def _to_trade(name: str, symbol: str, d: dict) -> Trade:
    # aggTrade : m = isBuyerMaker -> si True, l'AGRESSEUR (taker) est le VENDEUR.
    return Trade(
        exchange=name,
        symbol=symbol,
        price=float(d["p"]),
        size=float(d["q"]),
        side="sell" if d.get("m") else "buy",
        ts=int(d["T"]),
        trade_id=str(d["a"]),
    )


def fetch_before(market: str, symbol: str, before_id: str | None = None,
                 name: str = "binance", max_pages: int = 5,
                 pause: float = 0.1) -> list[Trade]:
    """Remonte le passe : un PAQUET de trades STRICTEMENT plus anciens que
    `before_id` (None -> repart des plus recents). aggTrades ne pagine QUE vers
    l'avant (fromId croissant) -> on descend par fenetres `fromId = id - 1000`.
    Renvoie une liste triee par ts croissant, dedupliquee. Symetrique de
    bitget_rest.fetch_before -> collecteur agnostique a l'exchange.
    """
    base, path = _base(market), _aggtrades_path(market)
    out: list[Trade] = []
    seen: set[str] = set()
    cur: int | None = int(before_id) if before_id else None
    for _ in range(max_pages):
        if cur is None:
            params = {"symbol": symbol, "limit": "1000"}          # plus recents
        else:
            if cur <= 0:
                break
            params = {"symbol": symbol, "fromId": str(max(0, cur - 1000)),
                      "limit": "1000"}
        page = _get(base, path, params)
        if not page:
            break
        ids: list[int] = []
        for d in page:
            aid = int(d["a"])
            if cur is not None and aid >= cur:   # garder STRICTEMENT plus ancien
                continue
            sid = str(aid)
            if sid in seen:
                continue
            seen.add(sid)
            out.append(_to_trade(name, symbol, d))
            ids.append(aid)
        if not ids:
            break
        cur = min(ids)                           # prochaine fenetre : plus ancien
        time.sleep(pause)   # respect rate limit
    out.sort(key=lambda t: t.ts)
    return out


def fetch_range(market: str, symbol: str, start_ms: int, end_ms: int,
                name: str = "binance", max_pages: int = 100,
                pause: float = 0.1) -> list[Trade]:
    """Trades agreges couvrant [start_ms, end_ms] par SEEK DIRECT (startTime), sans
    paginer depuis le present. Sert a combler un TROU precis cote Binance : on saute
    a son debut puis on pagine vers l'avant (fromId) jusqu'a depasser sa fin. Bien
    plus rapide que fetch_before pour un trou lointain (pas de re-parcours de la zone
    hors-trou). Binance accepte startTime ; Bitget Fills-History non -> Bitget garde
    fetch_before. Renvoie une liste triee par ts croissant, bornee a [start, end]."""
    base, path = _base(market), _aggtrades_path(market)
    out: list[Trade] = []
    seen: set[str] = set()
    params = {"symbol": symbol, "startTime": str(start_ms), "limit": "1000"}
    for _ in range(max_pages):
        page = _get(base, path, params)
        if not page:
            break
        last_t = 0
        for d in page:
            aid = str(d["a"])
            if aid in seen:
                continue
            seen.add(aid)
            out.append(_to_trade(name, symbol, d))
            last_t = int(d["T"])
        if len(page) < 1000 or last_t >= end_ms:   # trou couvert (ou plus de donnees)
            break
        params = {"symbol": symbol, "fromId": str(int(page[-1]["a"]) + 1), "limit": "1000"}
        time.sleep(pause)   # respect rate limit
    out = [t for t in out if start_ms <= t.ts <= end_ms]
    out.sort(key=lambda t: t.ts)
    return out


def fetch_since(market: str, symbol: str, start_ms: int,
                name: str = "binance", max_pages: int = 120,
                pause: float = 0.1) -> list[Trade]:
    """Trades agreges a partir de start_ms (vers le present), pagination par
    fromId. Renvoie une liste triee par ts croissant, dedupliquee."""
    base, path = _base(market), _aggtrades_path(market)
    now = int(time.time() * 1000)
    out: list[Trade] = []
    seen: set[str] = set()
    params = {"symbol": symbol, "startTime": str(start_ms), "limit": "1000"}
    for _ in range(max_pages):
        page = _get(base, path, params)
        if not page:
            break
        for d in page:
            aid = str(d["a"])
            if aid not in seen:
                seen.add(aid)
                out.append(_to_trade(name, symbol, d))
        last = page[-1]
        if len(page) < 1000 or int(last["T"]) >= now:
            break
        params = {"symbol": symbol, "fromId": str(int(last["a"]) + 1), "limit": "1000"}
        time.sleep(pause)   # respect rate limit
    out = [t for t in out if t.ts >= start_ms]
    out.sort(key=lambda t: t.ts)
    return out
