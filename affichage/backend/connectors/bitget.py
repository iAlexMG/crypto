"""Connecteur Bitget (exchange de REFERENCE).

Particularite Bitget : sur le WebSocket, le canal "books" envoie un
PREMIER SNAPSHOT puis des UPDATES incrementales, accompagnees d'un CHECKSUM
(CRC32) permettant de detecter une desynchronisation. Tout se fait via WS
(pas de snapshot REST necessaire, contrairement a d'autres exchanges).

Doc: wss://ws.bitget.com/v2/ws/public  (API v2, SPOT)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import zlib

import websockets

from ..models import OrderBook, Trade, now_ms
from .base import BaseConnector

log = logging.getLogger("connector.bitget")

WS_URL = "wss://ws.bitget.com/v2/ws/public"


class _Book:
    """Carnet local maintenu en strings (pour un checksum fidele a Bitget)."""

    def __init__(self) -> None:
        self.bids: dict[str, str] = {}  # price_str -> size_str
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

    def checksum(self) -> int:
        """CRC32 signe sur les 25 meilleurs niveaux, format Bitget :
        bid1p:bid1s:ask1p:ask1s:bid2p:... (niveaux manquants ignores)."""
        bids = self.sorted_bids()[:25]
        asks = self.sorted_asks()[:25]
        parts: list[str] = []
        for i in range(25):
            if i < len(bids):
                parts.append(bids[i][0])
                parts.append(bids[i][1])
            if i < len(asks):
                parts.append(asks[i][0])
                parts.append(asks[i][1])
        crc = zlib.crc32(":".join(parts).encode()) & 0xFFFFFFFF
        return crc - 0x100000000 if crc >= 0x80000000 else crc  # -> signe 32 bits


class BitgetConnector(BaseConnector):
    name = "bitget"

    # inst_type : "USDT-FUTURES" (defaut) ou "SPOT". meme endpoint WS v2.
    # feed_name : cle du flux dans le hub (permet de faire tourner SPOT et Futures
    # EN MEME TEMPS sous deux cles distinctes -- ex. "bitget" / "bitget_spot").
    def __init__(self, hub, symbols, depth: int = 25,
                 inst_type: str = "USDT-FUTURES", feed_name: str | None = None) -> None:
        super().__init__(hub, symbols, depth)
        self.inst_type = inst_type
        if feed_name:
            self.name = feed_name
        self._books: dict[str, _Book] = {s: _Book() for s in symbols}
        self._last_msg = 0.0
        self._last_trade = 0.0
        self._last_book = 0.0      # dernier push de carnet REUSSI (detection de gel)

    def _sub_args(self) -> list[dict]:
        # canal "books" = TOUS les niveaux de l'orderbook (snapshot+updates+checksum),
        # contrairement a books1/books5/books15 qui sont tronques.
        args = []
        for sym in self.symbols:
            args.append({"instType": self.inst_type, "channel": "books", "instId": sym})
            args.append({"instType": self.inst_type, "channel": "trade", "instId": sym})
        return args

    async def _stream(self) -> None:
        # ping_interval=None : on gere nous-memes le heartbeat applicatif Bitget
        # (texte "ping"/"pong"), comme exige par leur doc (sinon coupure ~30s).
        now = time.monotonic()
        self._last_msg = now
        self._last_trade = now
        self._last_book = now
        async with websockets.connect(WS_URL, ping_interval=None, max_size=2**22) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": self._sub_args()}))
            log.info("[bitget] souscrit a %s (%s)", self.symbols, self.inst_type)
            heartbeat = asyncio.create_task(self._heartbeat(ws))
            watchdog = asyncio.create_task(self._watchdog(ws))
            try:
                async for raw in ws:
                    self._last_msg = time.monotonic()
                    if raw == "pong":
                        continue
                    if raw == "ping":          # le serveur peut aussi pinger
                        await ws.send("pong")
                        continue
                    self._handle(json.loads(raw))
            finally:
                heartbeat.cancel()
                watchdog.cancel()

    async def _heartbeat(self, ws) -> None:
        """Bitget : le client DOIT envoyer "ping" regulierement (<30s) ; le
        serveur repond "pong". Sans cela la connexion est fermee."""
        while True:
            await asyncio.sleep(20)
            await ws.send("ping")

    async def _watchdog(self, ws) -> None:
        """Surveille la sante du flux et force une reconnexion si :
        - plus AUCUN message pendant 30s (connexion morte), OU
        - plus aucun TRADE pendant 120s (canal trade fige alors que le book
          continue -- le cas observe apres plusieurs heures), OU
        - le CARNET est fige 45s (resync bloquee, trades OK -> ni _last_msg ni
          _last_trade ne le voient ; cf. binance), OU
        - la session depasse 30 min (reconnexion preventive anti-degradation)."""
        start = time.monotonic()
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if (now - self._last_msg > 30 or now - self._last_trade > 120
                    or now - self._last_book > 45 or now - start > 1800):
                log.warning("[bitget] watchdog -> reconnexion (msg=%.0fs trade=%.0fs "
                            "book=%.0fs age=%.0fs)", now - self._last_msg,
                            now - self._last_trade, now - self._last_book, now - start)
                await ws.close()
                return

    def _handle(self, msg: dict) -> None:
        if msg.get("event") == "error":
            log.warning("[bitget] erreur souscription: %s", msg)
            return
        arg = msg.get("arg") or {}
        channel = arg.get("channel")
        symbol = arg.get("instId")
        action = msg.get("action")
        data = msg.get("data") or []
        if not channel or symbol not in self._books:
            return
        if channel == "books":
            self._on_book(action, symbol, data)
        elif channel == "trade" and action != "snapshot":
            # on ignore le snapshot initial (backfill de trades anterieurs) :
            # on ne veut afficher que les trades a partir du lancement
            self._on_trade(symbol, data)

    def _on_book(self, action: str, symbol: str, data: list) -> None:
        if not data:
            return
        book = self._books[symbol]
        entry = data[0]
        if action == "snapshot":
            book.reset()
        book.apply(entry.get("bids", []), entry.get("asks", []))

        # Validation checksum -> detecte une desync, declenche un resync (reconnect)
        expected = entry.get("checksum")
        if expected is not None and book.checksum() != int(expected):
            log.warning("[bitget] checksum KO sur %s -> resync", symbol)
            raise RuntimeError("orderbook checksum mismatch")

        ts = int(entry.get("ts", now_ms()))
        bids = [(float(p), float(s)) for p, s in book.sorted_bids()[: self.depth]]
        asks = [(float(p), float(s)) for p, s in book.sorted_asks()[: self.depth]]
        self._last_book = time.monotonic()           # carnet vivant (anti-gel watchdog)
        self.hub.set_orderbook(
            OrderBook(self.name, symbol, bids, asks, ts, synced=True)
        )

    def _on_trade(self, symbol: str, data: list) -> None:
        trades = [
            Trade(
                exchange=self.name,
                symbol=symbol,
                price=float(t["price"]),
                size=float(t["size"]),
                side="buy" if t.get("side") == "buy" else "sell",
                ts=int(t.get("ts", now_ms())),
                trade_id=str(t.get("tradeId", "")),
            )
            for t in data
        ]
        if trades:
            self._last_trade = time.monotonic()   # pour le watchdog
            self.hub.add_trades(self.name, symbol, trades)
