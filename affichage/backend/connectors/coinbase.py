"""Connecteur Coinbase (5e exchange) -- API Exchange (ex-Coinbase Pro).

SPOT UNIQUEMENT : l'API publique Coinbase Exchange ne couvre que le comptant
(paires USDT existantes : BTC-USDT, ETH-USDT). Pas de futures ici.

Tout en WS public (wss://ws-feed.exchange.coinbase.com), MEME famille que le REST
d'historique (api.exchange.coinbase.com) -> convention de `side` IDENTIQUE :
- Canal `matches` : trades. `side` = cote du MAKER (piege classique Coinbase) -> l'
  AGRESSEUR est l'OPPOSE : maker `sell` => un acheteur a tape l'ask => side "buy".
- Canal `level2_batch` : carnet (renvoie `level2_50`, 50 niveaux, public, sans auth ;
  le canal `level2` complet exige une authentification). `snapshot` (bids/asks complets)
  puis `l2update` (`changes` = [[side, price, size], ...], size "0" = suppression).
  PAS de numero de sequence ni de checksum sur level2_batch -> integrite best-effort :
  a la reconnexion on recoit un snapshot frais (le watchdog force la reco si gel).

Heartbeat : ping/pong PROTOCOLAIRE de la lib websockets (Coinbase n'exige pas de ping
applicatif). Symboles : product_id BASE-QUOTE (BTCUSDT <-> BTC-USDT), re-normalises
vers BTCUSDT pour le hub. Watchdog : msg/trade/CARNET fige/age (comme Binance).

Docs : docs/coinbase.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

import websockets

from ..models import OrderBook, Trade, now_ms
from .base import BaseConnector

log = logging.getLogger("connector.coinbase")

WS_URL = "wss://ws-feed.exchange.coinbase.com"


def product_id(symbol: str) -> str:
    """BTCUSDT -> BTC-USDT (Coinbase = BASE-QUOTE). Nos symboles sont *USDT."""
    return f"{symbol[:-4]}-{symbol[-4:]}"


def to_ms(iso: str | None) -> int:
    """ISO 8601 Coinbase ('...Z', microsecondes) -> epoch ms UTC. now_ms() si absent."""
    if not iso:
        return now_ms()
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return now_ms()


class _Book:
    """Carnet local (price_str -> size_str). level2 Coinbase : pas de sequence -> on
    applique snapshot puis changes ; resync = nouveau snapshot a la reconnexion."""

    def __init__(self) -> None:
        self.bids: dict[str, str] = {}
        self.asks: dict[str, str] = {}

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def set_snapshot(self, bids: list, asks: list) -> None:
        self.reset()
        for price, size in bids:
            self.bids[price] = size
        for price, size in asks:
            self.asks[price] = size

    def apply_changes(self, changes: list) -> None:
        for side, price, size in changes:
            book = self.bids if side == "buy" else self.asks
            if float(size) == 0:
                book.pop(price, None)
            else:
                book[price] = size

    def sorted_bids(self) -> list[tuple[str, str]]:
        return sorted(self.bids.items(), key=lambda kv: float(kv[0]), reverse=True)

    def sorted_asks(self) -> list[tuple[str, str]]:
        return sorted(self.asks.items(), key=lambda kv: float(kv[0]))


class CoinbaseConnector(BaseConnector):
    name = "coinbase"

    # market : "SPOT" uniquement (Coinbase n'a pas de futures ici). feed_name : cle hub.
    def __init__(self, hub, symbols, depth: int = 150,
                 market: str = "SPOT", feed_name: str | None = None) -> None:
        super().__init__(hub, symbols, depth)
        self.market = market
        if feed_name:
            self.name = feed_name
        self._books: dict[str, _Book] = {s: _Book() for s in symbols}
        self._synced: dict[str, bool] = {s: False for s in symbols}
        self._pid = {s: product_id(s) for s in symbols}     # commun -> product_id
        self._sym = {v: k for k, v in self._pid.items()}     # product_id -> commun
        self._last_msg = 0.0
        self._last_trade = 0.0
        self._last_book = 0.0      # dernier push de carnet REUSSI (detection de gel)

    async def _stream(self) -> None:
        now = time.monotonic()
        self._last_msg = now
        self._last_trade = now
        self._last_book = now
        for s in self.symbols:                       # repart "non synchronise" a chaque (re)connexion
            self._books[s].reset()
            self._synced[s] = False
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20,
                                      max_size=2**22) as ws:
            await ws.send(json.dumps({
                "type": "subscribe",
                "product_ids": list(self._pid.values()),
                "channels": ["matches", "level2_batch"],
            }))
            log.info("[%s] souscrit a %s (%s)", self.name, list(self._pid.values()), self.market)
            watchdog = asyncio.create_task(self._watchdog(ws))
            try:
                async for raw in ws:
                    self._last_msg = time.monotonic()
                    self._handle(json.loads(raw))
            finally:
                watchdog.cancel()

    async def _watchdog(self, ws) -> None:
        """Reconnexion forcee si : aucun message 30s, aucun trade 120s, CARNET fige
        45s (resync level2 bloquee alors que les trades continuent), ou session > 30 min."""
        start = time.monotonic()
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if (now - self._last_msg > 30 or now - self._last_trade > 120
                    or now - self._last_book > 45 or now - start > 1800):
                log.warning("[%s] watchdog -> reconnexion (msg=%.0fs trade=%.0fs "
                            "book=%.0fs age=%.0fs)", self.name, now - self._last_msg,
                            now - self._last_trade, now - self._last_book, now - start)
                await ws.close()
                return

    def _handle(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "error":
            log.warning("[%s] erreur: %s", self.name, msg.get("message"))
            return
        symbol = self._sym.get(msg.get("product_id"))
        if symbol is None:
            return
        if mtype == "snapshot":
            self._on_snapshot(symbol, msg)
        elif mtype == "l2update":
            self._on_l2update(symbol, msg)
        elif mtype == "match":                       # (last_match ignore -> pas de doublon)
            self._on_trade(symbol, msg)

    def _on_snapshot(self, symbol: str, msg: dict) -> None:
        book = self._books[symbol]
        book.set_snapshot(msg.get("bids", []), msg.get("asks", []))
        self._synced[symbol] = True
        self._push(symbol, now_ms())

    def _on_l2update(self, symbol: str, msg: dict) -> None:
        if not self._synced[symbol]:
            return                                   # pas encore de snapshot
        self._books[symbol].apply_changes(msg.get("changes", []))
        self._push(symbol, to_ms(msg.get("time")))

    def _push(self, symbol: str, ts: int) -> None:
        book = self._books[symbol]
        bids = [(float(p), float(s)) for p, s in book.sorted_bids()[: self.depth]]
        asks = [(float(p), float(s)) for p, s in book.sorted_asks()[: self.depth]]
        if not bids or not asks:
            return
        self.hub.set_orderbook(OrderBook(self.name, symbol, bids, asks, ts, synced=True))
        self._last_book = time.monotonic()           # carnet vivant (anti-gel watchdog)

    def _on_trade(self, symbol: str, t: dict) -> None:
        try:
            trade = Trade(
                exchange=self.name,
                symbol=symbol,
                price=float(t["price"]),
                size=float(t["size"]),
                # `side` Coinbase = cote du MAKER -> agresseur = OPPOSE.
                side="buy" if t.get("side") == "sell" else "sell",
                ts=to_ms(t.get("time")),
                trade_id=str(t.get("trade_id", "")),
            )
        except (KeyError, ValueError):
            return
        self._last_trade = time.monotonic()
        self.hub.add_trades(self.name, symbol, [trade])
