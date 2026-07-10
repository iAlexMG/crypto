"""Connecteur OKX (4e exchange) -- API v5.

Proche de Bitget : tout en WS, carnet snapshot+updates, heartbeat texte "ping"/"pong".
Differences :
- **Symboles** : OKX utilise `BTC-USDT` (spot) / `BTC-USDT-SWAP` (perp). On mappe le
  symbole commun (BTCUSDT) <-> instId OKX, et on RE-normalise vers BTCUSDT pour le hub.
- **Entrees carnet a 4 champs** : `[px, sz, "0"(deprecated), nbOrders]` -> on lit px/sz.
- Canal `books` (400 niveaux) : `action=snapshot` puis `action=update`. INTEGRITE par
  **chainage de sequence** `seqId`/`prevSeqId` (le `prevSeqId` d'un update doit egaler
  le `seqId` precedent ; sinon TROU -> resync). OKX fournit aussi un `checksum` CRC32,
  NON utilise ici (invalidable depuis un environnement geo-restreint, et le seqId
  suffit). Taille `"0"` = suppression.
- Trades : canal `trades`, `side` = cote de l'AGRESSEUR (buy = a tape l'ask).

Docs : wss://ws.okx.com:8443/ws/v5/public (public, spot + swap, meme endpoint).
NB : OKX geo-restreint certaines regions -> WS/REST peuvent etre indisponibles selon
le reseau (cf. docs/okx.md).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets

from ..models import OrderBook, Trade, now_ms
from .base import BaseConnector

log = logging.getLogger("connector.okx")

WS_URL = "wss://ws.okx.com:8443/ws/v5/public"

# OKX SWAP : `sz` est en CONTRATS -> quantite en actif de base = sz x ctVal. Sans ca,
# BTC-USDT-SWAP est stocke 100x trop gros (et ETH 10x) -> volume hybride fausse, points
# scatter geants. Confirme en live le 2026-06-27 (GET /api/v5/public/instruments). Spot = 1.0.
CONTRACT_VAL: dict[str, float] = {"BTCUSDT": 0.01, "ETHUSDT": 0.1}


def inst_id(market: str, symbol: str) -> str:
    """BTCUSDT -> BTC-USDT (spot) / BTC-USDT-SWAP (futures). Nos symboles sont *USDT."""
    base, quote = symbol[:-4], symbol[-4:]
    pair = f"{base}-{quote}"
    return f"{pair}-SWAP" if market == "FUTURES" else pair


class _Book:
    """Carnet local en strings (checksum fidele a OKX). Entrees a 4 champs -> px/sz."""

    def __init__(self) -> None:
        self.bids: dict[str, str] = {}
        self.asks: dict[str, str] = {}

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply(self, bids: list, asks: list) -> None:
        for entry in bids:
            price, size = entry[0], entry[1]
            if float(size) == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = size
        for entry in asks:
            price, size = entry[0], entry[1]
            if float(size) == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = size

    def sorted_bids(self) -> list[tuple[str, str]]:
        return sorted(self.bids.items(), key=lambda kv: float(kv[0]), reverse=True)

    def sorted_asks(self) -> list[tuple[str, str]]:
        return sorted(self.asks.items(), key=lambda kv: float(kv[0]))


class OkxConnector(BaseConnector):
    name = "okx"

    # market : "FUTURES" (swap USDT perp) ou "SPOT". feed_name : cle du flux dans le hub.
    def __init__(self, hub, symbols, depth: int = 150,
                 market: str = "FUTURES", feed_name: str | None = None) -> None:
        super().__init__(hub, symbols, depth)
        self.market = market
        if feed_name:
            self.name = feed_name
        self._books: dict[str, _Book] = {s: _Book() for s in symbols}
        # SWAP : contrats -> actif de base (x ctVal). SPOT : deja en actif de base (1.0).
        self._mult = {s: (CONTRACT_VAL.get(s, 1.0) if market == "FUTURES" else 1.0)
                      for s in symbols}
        self._inst = {s: inst_id(market, s) for s in symbols}     # commun -> instId
        self._sym = {v: k for k, v in self._inst.items()}         # instId -> commun
        self._last_seq: dict[str, int] = {s: -1 for s in symbols}
        self._synced: dict[str, bool] = {s: False for s in symbols}
        self._last_msg = 0.0
        self._last_trade = 0.0
        self._last_book = 0.0      # dernier push de carnet REUSSI (detection de gel)

    def _sub_args(self) -> list[dict]:
        args = []
        for sym in self.symbols:
            iid = self._inst[sym]
            args.append({"channel": "books", "instId": iid})
            args.append({"channel": "trades", "instId": iid})
        return args

    async def _stream(self) -> None:
        # ping_interval=None : heartbeat applicatif OKX (texte "ping"/"pong", <30s).
        now = time.monotonic()
        self._last_msg = now
        self._last_trade = now
        self._last_book = now
        for s in self.symbols:                       # repart "non synchronise" a chaque (re)connexion
            self._books[s].reset()
            self._synced[s] = False
            self._last_seq[s] = -1
        async with websockets.connect(WS_URL, ping_interval=None, max_size=2**22) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": self._sub_args()}))
            log.info("[%s] souscrit a %s (%s)", self.name, list(self._inst.values()), self.market)
            heartbeat = asyncio.create_task(self._heartbeat(ws))
            watchdog = asyncio.create_task(self._watchdog(ws))
            try:
                async for raw in ws:
                    self._last_msg = time.monotonic()
                    if raw == "pong":
                        continue
                    self._handle(json.loads(raw))
            finally:
                heartbeat.cancel()
                watchdog.cancel()

    async def _heartbeat(self, ws) -> None:
        """OKX : envoyer "ping" (<30s) sinon la connexion est fermee ; reponse "pong"."""
        while True:
            await asyncio.sleep(20)
            await ws.send("ping")

    async def _watchdog(self, ws) -> None:
        """Reconnexion si : aucun message 30s, aucun trade 120s, CARNET fige 45s (resync
        bloquee, trades OK -> ni _last_msg ni _last_trade ne le voient ; cf. binance),
        ou session > 30 min."""
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
        if msg.get("event"):                          # subscribe ack / error
            if msg.get("event") == "error":
                log.warning("[%s] erreur souscription: %s", self.name, msg)
            return
        arg = msg.get("arg") or {}
        channel = arg.get("channel")
        symbol = self._sym.get(arg.get("instId"))     # instId -> symbole commun
        data = msg.get("data") or []
        if symbol is None or not data:
            return
        if channel == "books":
            self._on_book(msg.get("action"), symbol, data)
        elif channel == "trades":
            self._on_trade(symbol, data)

    def _on_book(self, action: str, symbol: str, data: list) -> None:
        book = self._books[symbol]
        entry = data[0]
        seq = int(entry.get("seqId", -1))
        prev = int(entry.get("prevSeqId", -1))
        if action == "snapshot":
            book.reset()
            book.apply(entry.get("bids", []), entry.get("asks", []))
            self._last_seq[symbol] = seq
            self._synced[symbol] = True
        else:                                        # update
            if not self._synced[symbol]:
                return                               # pas encore de snapshot
            last = self._last_seq[symbol]
            if seq == last:
                return                               # inchange (OKX peut renvoyer seq==prev==last)
            if seq < last:
                return                               # doublon / ancien
            if prev != last:                         # chainage rompu -> TROU -> resync
                log.warning("[%s] %s trou de sequence (prev=%d, last=%d) -> resync",
                            self.name, symbol, prev, last)
                self._synced[symbol] = False
                raise RuntimeError("orderbook sequence gap")
            book.apply(entry.get("bids", []), entry.get("asks", []))
            self._last_seq[symbol] = seq
        ts = int(entry.get("ts", now_ms()))
        mult = self._mult.get(symbol, 1.0)               # contrats -> actif de base (SWAP)
        bids = [(float(p), float(s) * mult) for p, s in book.sorted_bids()[: self.depth]]
        asks = [(float(p), float(s) * mult) for p, s in book.sorted_asks()[: self.depth]]
        if not bids or not asks:
            return
        self._last_book = time.monotonic()               # carnet vivant (anti-gel watchdog)
        self.hub.set_orderbook(OrderBook(self.name, symbol, bids, asks, ts, synced=True))

    def _on_trade(self, symbol: str, data: list) -> None:
        trades = [
            Trade(
                exchange=self.name,
                symbol=symbol,
                price=float(t["px"]),
                size=float(t["sz"]) * self._mult.get(symbol, 1.0),  # contrats -> actif de base
                side="buy" if t.get("side") == "buy" else "sell",   # cote agresseur
                ts=int(t.get("ts", now_ms())),
                trade_id=str(t.get("tradeId", "")),
            )
            for t in data
        ]
        if trades:
            self._last_trade = time.monotonic()
            self.hub.add_trades(self.name, symbol, trades)
