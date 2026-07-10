"""Connecteur Bybit (3e exchange) -- API v5.

Particularite Bybit (proche de Bitget : tout en WS, pas de snapshot REST) : le topic
`orderbook.{depth}.{symbol}` envoie un message `type=snapshot` (carnet complet) puis
des `type=delta` incrementaux. Integrite par NUMERO DE SEQUENCE `u` (PAS de checksum) :
chaque delta doit s'enchainer (`u == last_u + 1`) ; un saut -> TROU -> on force une
reconnexion (le WS renvoie alors un snapshot frais). Bybit peut aussi renvoyer
spontanement un `type=snapshot` (redemarrage de service) -> on reset le carnet local.
Taille `"0"` = suppression du niveau.

Trades : topic `publicTrade.{symbol}`. Champ `S` = cote de l'AGRESSEUR (Buy = un
acheteur a tape l'ask -> side "buy"). id = `i`, ts = `T` (ms), prix `p`, taille `v`.

Heartbeat : Bybit exige un ping APPLICATIF `{"op":"ping"}` (<20s) ; le serveur repond
`{"op":"pong"}`. Symboles deja au format commun (BTCUSDT). Watchdog identique aux autres.

Docs : SPOT wss://stream.bybit.com/v5/public/spot
       LINEAR (USDT perp) wss://stream.bybit.com/v5/public/linear
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets

from ..models import OrderBook, Trade, now_ms
from .base import BaseConnector

log = logging.getLogger("connector.bybit")

SPOT_WS = "wss://stream.bybit.com/v5/public/spot"
LINEAR_WS = "wss://stream.bybit.com/v5/public/linear"

# Profondeur du topic orderbook (paliers Bybit fixes : 1/50/200/1000). 200 @100ms =
# bon compromis (couvre la profondeur d'affichage du DOM sans surcharger).
WS_DEPTH = 200


class _Book:
    """Carnet local (price_str -> size_str). Integrite par sequence `u` (pas de checksum)."""

    def __init__(self) -> None:
        self.bids: dict[str, str] = {}
        self.asks: dict[str, str] = {}

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply(self, bids: list, asks: list) -> None:
        for price, size in bids:
            if float(size) == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = size
        for price, size in asks:
            if float(size) == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = size

    def sorted_bids(self) -> list[tuple[str, str]]:
        return sorted(self.bids.items(), key=lambda kv: float(kv[0]), reverse=True)

    def sorted_asks(self) -> list[tuple[str, str]]:
        return sorted(self.asks.items(), key=lambda kv: float(kv[0]))


class BybitConnector(BaseConnector):
    name = "bybit"

    # market : "FUTURES" (linear USDT perp) ou "SPOT". feed_name : cle du flux dans le hub.
    def __init__(self, hub, symbols, depth: int = 150,
                 market: str = "FUTURES", feed_name: str | None = None) -> None:
        super().__init__(hub, symbols, depth)
        self.market = market
        if feed_name:
            self.name = feed_name
        self._books: dict[str, _Book] = {s: _Book() for s in symbols}
        self._last_u: dict[str, int] = {s: 0 for s in symbols}
        self._synced: dict[str, bool] = {s: False for s in symbols}
        self._last_msg = 0.0
        self._last_trade = 0.0
        self._last_book = 0.0      # dernier push de carnet REUSSI (detection de gel)

    def _ws_url(self) -> str:
        return LINEAR_WS if self.market == "FUTURES" else SPOT_WS

    def _topics(self) -> list[str]:
        out = []
        for sym in self.symbols:
            out.append(f"orderbook.{WS_DEPTH}.{sym}")
            out.append(f"publicTrade.{sym}")
        return out

    async def _stream(self) -> None:
        now = time.monotonic()
        self._last_msg = now
        self._last_trade = now
        self._last_book = now
        for s in self.symbols:                       # repart "non synchronise" a chaque (re)connexion
            self._synced[s] = False
            self._books[s].reset()
        # ping_interval=None : on gere le heartbeat applicatif Bybit ({"op":"ping"}).
        async with websockets.connect(self._ws_url(), ping_interval=None,
                                      max_size=2**22) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": self._topics()}))
            log.info("[%s] souscrit a %s (%s)", self.name, self.symbols, self.market)
            heartbeat = asyncio.create_task(self._heartbeat(ws))
            watchdog = asyncio.create_task(self._watchdog(ws))
            try:
                async for raw in ws:
                    self._last_msg = time.monotonic()
                    self._handle(json.loads(raw))
            finally:
                heartbeat.cancel()
                watchdog.cancel()

    async def _heartbeat(self, ws) -> None:
        """Bybit : le client DOIT envoyer {"op":"ping"} regulierement (<20s)."""
        while True:
            await asyncio.sleep(20)
            await ws.send(json.dumps({"op": "ping"}))

    async def _watchdog(self, ws) -> None:
        """Reconnexion forcee si : aucun message 30s, aucun trade 120s, CARNET fige
        45s, ou session > 30 min. Le gel du CARNET (resync bloquee, trades OK) n'est vu
        ni par _last_msg ni par _last_trade -> suivi dedie _last_book (cf. binance)."""
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
        topic = msg.get("topic")
        if not topic:                                # pong / ack de souscription -> ignore
            return
        data = msg.get("data")
        if topic.startswith("orderbook."):
            # Bybit v5 : `ts` (horloge exchange) est a la RACINE du message, pas dans
            # `data` -> le transmettre explicitement (sinon repli sur l'horloge locale).
            self._on_book(topic.rsplit(".", 1)[-1], msg.get("type"), data, msg.get("ts"))
        elif topic.startswith("publicTrade."):
            self._on_trade(topic.rsplit(".", 1)[-1], data)

    def _on_book(self, symbol: str, mtype: str, d: dict, root_ts=None) -> None:
        if symbol not in self._books or not d:
            return
        book = self._books[symbol]
        u = int(d.get("u", 0))
        if mtype == "snapshot":
            book.reset()
            book.apply(d.get("b", []), d.get("a", []))
            self._last_u[symbol] = u
            self._synced[symbol] = True
        else:                                        # delta
            if not self._synced[symbol]:
                return                               # pas encore de snapshot -> on attend
            last = self._last_u[symbol]
            if u <= last:
                return                               # doublon / ancien
            if u != last + 1:                        # TROU de sequence -> resync (reconnexion)
                log.warning("[%s] %s trou de sequence (u=%d, last=%d) -> resync",
                            self.name, symbol, u, last)
                self._synced[symbol] = False
                raise RuntimeError("orderbook sequence gap")
            book.apply(d.get("b", []), d.get("a", []))
            self._last_u[symbol] = u
        ts = int(root_ts or d.get("ts") or now_ms())   # v5 : ts a la racine du message
        bids = [(float(p), float(s)) for p, s in book.sorted_bids()[: self.depth]]
        asks = [(float(p), float(s)) for p, s in book.sorted_asks()[: self.depth]]
        if not bids or not asks:
            return
        self._last_book = time.monotonic()           # carnet vivant (anti-gel watchdog)
        self.hub.set_orderbook(OrderBook(self.name, symbol, bids, asks, ts, synced=True))

    def _on_trade(self, symbol: str, data: list) -> None:
        if symbol not in self._books or not data:
            return
        trades = [
            Trade(
                exchange=self.name,
                symbol=symbol,
                price=float(t["p"]),
                size=float(t["v"]),
                side="buy" if t.get("S") == "Buy" else "sell",   # S = cote agresseur
                ts=int(t.get("T", now_ms())),
                trade_id=str(t.get("i", "")),
            )
            for t in data
        ]
        if trades:
            self._last_trade = time.monotonic()
            self.hub.add_trades(self.name, symbol, trades)
