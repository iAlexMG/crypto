"""Client REST OKX (v5) : historique des trades (history-trades, paginable).

Contrairement a Bybit, OKX EXPOSE un historique de trades paginable :
- `GET /api/v5/market/history-trades?instId=...&type=1&after=<tradeId>&limit=100`
  renvoie des trades plus ANCIENS que `after` (pagination par tradeId, comme l'
  `idLessThan` de Bitget). type=1 (defaut) = pagination par tradeId.
Donc le collecteur a un support COMPLET (comblage de trous + remontee du passe), via
fetch_before. Pas de seek par temps cote service (fetch_range = None) : le comblage
amorce fetch_before au bord du trou (suffisant ; l'id WS == l'id REST chez OKX).

Symboles : instId OKX (BTC-USDT-SWAP / BTC-USDT) derive du symbole commun (BTCUSDT).
Endpoint public (pas d'auth). limit max = 100 par page.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

from ..models import Trade
from .okx import CONTRACT_VAL, inst_id

log = logging.getLogger("connector.okx.rest")

BASE = "https://www.okx.com"
HIST_PATH = "/api/v5/market/history-trades"

# OKX filtre les requetes sans User-Agent "navigateur" (le defaut Python-urllib
# renvoie HTTP 403 Forbidden via leur WAF). On en envoie donc un explicite.
_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}


def _get(path: str, params: dict) -> list[dict]:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        payload = json.load(r)
    if payload.get("code") != "0":
        raise RuntimeError(f"okx rest {payload.get('code')}: {payload.get('msg')}")
    return payload.get("data") or []


def _mult_for(market: str, symbol: str) -> float:
    """SWAP : `sz` est en CONTRATS -> actif de base = sz x ctVal (cf. okx.py). SPOT : 1.0.
    Identique au live (okx._mult) : sans ca le REST stocke BTC 100x / ETH 10x trop gros."""
    return CONTRACT_VAL.get(symbol, 1.0) if market == "FUTURES" else 1.0


def _to_trade(name: str, symbol: str, d: dict, mult: float = 1.0) -> Trade:
    return Trade(
        exchange=name,
        symbol=symbol,
        price=float(d["px"]),
        size=float(d["sz"]) * mult,
        side="buy" if d.get("side") == "buy" else "sell",
        ts=int(d["ts"]),
        trade_id=str(d.get("tradeId", "")),
    )


def fetch_page(market: str, symbol: str, after: str | None = None,
               limit: int = 100) -> list[dict]:
    """Une page d'history-trades (les plus recents, ou plus anciens que `after`).
    Triee du plus RECENT au plus ancien (comme Bitget)."""
    params = {"instId": inst_id(market, symbol), "type": "1", "limit": str(limit)}
    if after:
        params["after"] = after
    return _get(HIST_PATH, params)


def fetch_before(market: str, symbol: str, before_id: str | None = None,
                 name: str = "okx", max_pages: int = 5,
                 pause: float = 0.12) -> list[Trade]:
    """Remonte le passe : un PAQUET de trades STRICTEMENT plus anciens que `before_id`
    (None -> repart des plus recents). Pagine via `after`=tradeId. Renvoie une liste
    triee par ts croissant, dedupliquee. Symetrique des autres exchanges."""
    seen: set[str] = set()
    out: list[Trade] = []
    mult = _mult_for(market, symbol)
    after: str | None = str(before_id) if before_id else None
    for _ in range(max_pages):
        page = fetch_page(market, symbol, after)
        if not page:
            break
        for d in page:
            tid = str(d.get("tradeId", ""))
            if tid and tid not in seen:
                seen.add(tid)
                out.append(_to_trade(name, symbol, d, mult))
        after = str(page[-1].get("tradeId", ""))     # page triee recent->ancien
        if not after:
            break
        time.sleep(pause)   # respect rate limit
    out.sort(key=lambda t: t.ts)
    return out


def fetch_since(market: str, symbol: str, start_ms: int,
                name: str = "okx", max_pages: int = 60,
                pause: float = 0.12) -> list[Trade]:
    """Remonte les trades jusqu'a start_ms (du plus recent au plus ancien), en
    paginant. Renvoie une liste triee par ts croissant, dedupliquee."""
    seen: set[str] = set()
    out: list[Trade] = []
    mult = _mult_for(market, symbol)
    after: str | None = None
    for _ in range(max_pages):
        page = fetch_page(market, symbol, after)
        if not page:
            break
        oldest_ts = int(page[-1]["ts"])
        for d in page:
            tid = str(d.get("tradeId", ""))
            if tid and tid not in seen:
                seen.add(tid)
                out.append(_to_trade(name, symbol, d, mult))
        after = str(page[-1].get("tradeId", ""))
        if oldest_ts <= start_ms or not after:
            break
        time.sleep(pause)   # respect rate limit
    out = [t for t in out if t.ts >= start_ms]
    out.sort(key=lambda t: t.ts)
    return out
