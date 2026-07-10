"""Connecteur Binance (2e exchange).

Particularite Binance (DIFFERENTE de Bitget) : l'orderbook s'amorce par un
**snapshot REST** puis se met a jour via des **diffs WebSocket** raccordees par
**numero de sequence** (PAS de checksum). Algorithme officiel :

  1. Ouvrir le flux WS `<sym>@depth` et BUFFERISER les evenements.
  2. Recuperer le snapshot REST (`lastUpdateId`).
  3. Jeter les events dont `u` <= lastUpdateId.
  4. Le 1er event applique doit verifier `U` <= lastUpdateId+1 <= `u` (SPOT)
     / `U` <= lastUpdateId <= `u` (FUTURES).
  5. Continuite : chaque event suivant doit s'enchainer (SPOT : U == last_u+1 ;
     FUTURES : pu == last_u). Sinon -> TROU -> resynchronisation (nouveau snapshot).

Trades : flux `<sym>@aggTrade`. `m`=isBuyerMaker -> agresseur = vendeur si True.
Symboles deja au format Bitget (BTCUSDT). Heartbeat : gere par la lib websockets
(ping/pong protocolaire automatique). Watchdog identique a Bitget.

Docs : SPOT wss://stream.binance.com:9443 , FUTURES wss://fstream.binance.com
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets

from ..models import OrderBook, Trade, now_ms
from .base import BaseConnector
from . import binance_rest

log = logging.getLogger("connector.binance")

SPOT_WS = "wss://stream.binance.com:9443"
FUT_WS = "wss://fstream.binance.com"


class _Book:
    """Carnet local (price_str -> qty_str). Pas de checksum chez Binance :
    l'integrite repose sur la continuite des numeros de sequence."""

    def __init__(self) -> None:
        self.bids: dict[str, str] = {}
        self.asks: dict[str, str] = {}

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def apply(self, bids: list, asks: list) -> None:
        for price, qty in bids:
            if float(qty) == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty
        for price, qty in asks:
            if float(qty) == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

    def sorted_bids(self) -> list[tuple[str, str]]:
        return sorted(self.bids.items(), key=lambda kv: float(kv[0]), reverse=True)

    def sorted_asks(self) -> list[tuple[str, str]]:
        return sorted(self.asks.items(), key=lambda kv: float(kv[0]))


class BinanceConnector(BaseConnector):
    name = "binance"

    # market : "FUTURES" (USDT-M) ou "SPOT". feed_name : cle du flux dans le hub.
    def __init__(self, hub, symbols, depth: int = 150,
                 market: str = "FUTURES", feed_name: str | None = None) -> None:
        super().__init__(hub, symbols, depth)
        self.market = market
        if feed_name:
            self.name = feed_name
        self._books: dict[str, _Book] = {s: _Book() for s in symbols}
        self._buf: dict[str, list] = {s: [] for s in symbols}     # events en attente du snapshot
        self._synced: dict[str, bool] = {s: False for s in symbols}
        self._snapping: dict[str, bool] = {s: False for s in symbols}  # snapshot en cours (anti-concurrence)
        self._last_u: dict[str, int] = {s: 0 for s in symbols}
        self._last_msg = 0.0
        self._last_trade = 0.0
        self._last_book = 0.0      # dernier push de carnet REUSSI (detection de gel)

    def _ws_url(self) -> str:
        base = FUT_WS if self.market == "FUTURES" else SPOT_WS
        return f"{base}/ws"

    def _streams(self) -> list[str]:
        # Trades : SPOT -> @aggTrade (OK). FUTURES -> @trade : sur fstream le flux
        # @aggTrade ne pousse RIEN (constate en direct), alors que @trade (trades
        # individuels) fonctionne. Meme mapping (m=isBuyerMaker, p/q/T), id = `t`.
        trade_stream = "trade" if self.market == "FUTURES" else "aggTrade"
        out = []
        for sym in self.symbols:
            s = sym.lower()
            out.append(f"{s}@depth@100ms")
            out.append(f"{s}@{trade_stream}")
        return out

    async def _stream(self) -> None:
        now = time.monotonic()
        self._last_msg = now
        self._last_trade = now
        self._last_book = now
        for s in self.symbols:                       # repart "non synchronise" a chaque (re)connexion
            self._synced[s] = False
            self._buf[s] = []
            self._books[s].reset()
        async with websockets.connect(self._ws_url(), ping_interval=20,
                                      ping_timeout=20, max_size=2**22) as ws:
            # souscription explicite (methode SUBSCRIBE) -> events non encapsules
            await ws.send(json.dumps({"method": "SUBSCRIBE",
                                      "params": self._streams(), "id": 1}))
            log.info("[%s] souscrit a %s (%s)", self.name, self.symbols, self.market)
            watchdog = asyncio.create_task(self._watchdog(ws))
            snaps = [asyncio.create_task(self._snapshot(s)) for s in self.symbols]
            try:
                async for raw in ws:
                    self._last_msg = time.monotonic()
                    msg = json.loads(raw)
                    data = msg.get("data") or msg     # flux combine -> {stream, data}
                    self._handle(data)
            finally:
                watchdog.cancel()
                for t in snaps:
                    t.cancel()

    async def _snapshot(self, symbol: str) -> None:
        """Recupere le snapshot REST puis raccorde les diffs en attente. Gere le
        cas ou le snapshot est decale par rapport au flux (trop ancien -> il
        manque le pont ; trop recent -> aucun diff ne le couvre encore) : on
        attend/refetch jusqu'a avoir un recouvrement -> pas de boucle de resync.
        Garde anti-concurrence : un seul snapshot a la fois par symbole."""
        if self._snapping.get(symbol):
            return
        self._snapping[symbol] = True
        loop = asyncio.get_running_loop()
        try:
            for _ in range(20):
                if self._stop.is_set() or self._synced[symbol]:
                    return
                try:
                    last_id, bids, asks = await loop.run_in_executor(
                        None, binance_rest.fetch_depth, self.market, symbol, 1000)
                except Exception as exc:  # noqa: BLE001 - reseau -> on reessaie
                    log.warning("[%s] snapshot %s echec: %s", self.name, symbol, exc)
                    await asyncio.sleep(1.0)
                    continue
                buf = self._buf[symbol]
                if buf and buf[0]["U"] > last_id + 1:
                    await asyncio.sleep(0.3)               # snapshot trop ANCIEN -> refetch plus recent
                    continue
                if buf and buf[-1]["u"] < last_id:
                    await asyncio.sleep(0.3)               # snapshot trop RECENT -> attendre des diffs
                    continue
                self._seed(symbol, last_id, bids, asks)
                return
        finally:
            self._snapping[symbol] = False

    def _seed(self, symbol: str, last_id: int, bids: list, asks: list) -> None:
        """Pose le snapshot puis applique les diffs bufferises a partir du pont
        (U <= lastUpdateId+1 <= u pour SPOT, U <= lastUpdateId <= u pour FUTURES).
        Synchrone -> pas d'await, donc pas de course."""
        book = self._books[symbol]
        book.reset()
        book.apply(bids, asks)
        spot = self.market != "FUTURES"
        applied_first = False
        for ev in self._buf[symbol]:
            if ev["u"] < last_id:
                continue                                   # strictement plus ancien -> jeter
            if not applied_first:
                ok = (ev["U"] <= last_id + 1) if spot else (ev["U"] <= last_id)
                if not ok:
                    continue                               # pas encore le pont
                applied_first = True
            book.apply(ev.get("b", []), ev.get("a", []))
            self._last_u[symbol] = ev["u"]
        if not applied_first:
            self._last_u[symbol] = last_id                 # buffer vide/aucun pont -> on suit le snapshot
        self._buf[symbol] = []
        self._synced[symbol] = True
        self._push(symbol, now_ms())
        log.info("[%s] %s synchronise (lastUpdateId=%d)", self.name, symbol, last_id)

    def _handle(self, data: dict) -> None:
        etype = data.get("e")
        symbol = data.get("s")
        if symbol not in self._books:
            return
        if etype == "depthUpdate":
            self._on_depth(symbol, data)
        elif etype in ("aggTrade", "trade"):   # SPOT=aggTrade, FUTURES=trade
            self._on_trade(symbol, data)

    def _on_depth(self, symbol: str, ev: dict) -> None:
        if not self._synced[symbol]:
            self._buf[symbol].append(ev)                   # en attente du snapshot
            return
        U, u = ev["U"], ev["u"]
        last_u = self._last_u[symbol]
        # continuite de sequence : SPOT -> U == last_u+1 ; FUTURES -> pu == last_u
        contiguous = (ev.get("pu") == last_u) if self.market == "FUTURES" else (U == last_u + 1)
        if not contiguous and u <= last_u:
            return                                         # doublon/ancien -> ignore
        if not contiguous:
            log.warning("[%s] %s trou de sequence -> resync", self.name, symbol)
            self._synced[symbol] = False
            self._buf[symbol] = [ev]
            asyncio.create_task(self._snapshot(symbol))
            return
        self._books[symbol].apply(ev.get("b", []), ev.get("a", []))
        self._last_u[symbol] = u
        self._push(symbol, int(ev.get("E", now_ms())))

    def _push(self, symbol: str, ts: int) -> None:
        book = self._books[symbol]
        bids = [(float(p), float(q)) for p, q in book.sorted_bids()[: self.depth]]
        asks = [(float(p), float(q)) for p, q in book.sorted_asks()[: self.depth]]
        if not bids or not asks:
            return
        self.hub.set_orderbook(OrderBook(self.name, symbol, bids, asks, ts, synced=True))
        self._last_book = time.monotonic()           # carnet vivant (anti-gel watchdog)

    def _on_trade(self, symbol: str, d: dict) -> None:
        # Le flux @trade des FUTURES emet des trades NON-MARKET (X="NA", prix 0 :
        # fonds d'assurance / ADL). Non filtres, ils donnent une meche aberrante
        # (low a 0) et une colonne footprint degeneree (range 0..prix). On ne
        # garde que les vrais trades de marche. (SPOT @aggTrade n'a pas de champ X
        # -> defaut "MARKET" -> non filtre.)
        if d.get("X", "MARKET") != "MARKET":
            return
        price = float(d["p"])
        if price <= 0:
            return
        trade = Trade(
            exchange=self.name,
            symbol=symbol,
            price=price,
            size=float(d["q"]),
            side="sell" if d.get("m") else "buy",   # m=isBuyerMaker -> agresseur=vendeur
            ts=int(d.get("T", now_ms())),
            trade_id=str(d.get("a") or d.get("t") or ""),  # aggTrade=`a`, trade=`t`
        )
        self._last_trade = time.monotonic()
        self.hub.add_trades(self.name, symbol, [trade])

    async def _watchdog(self, ws) -> None:
        """Reconnexion forcee si : aucun message 30s, aucun trade 120s, CARNET fige
        45s, ou session > 30 min. Le gel du CARNET (depth) est crucial : une resync
        bloquee laisse le carnet fige alors que les TRADES continuent -> ni _last_msg
        ni _last_trade ne le detectent (ils restent frais). Sur BTC/ETH @depth@100ms,
        45s sans push = anomalie (les mises a jour arrivent en continu)."""
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
