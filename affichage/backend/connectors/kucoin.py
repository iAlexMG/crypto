"""Connecteur KuCoin (6e exchange) -- Spot + Futures.

KuCoin Spot (api.kucoin.com) et Futures (api-futures.kucoin.com) sont deux HOSTS
distincts mais PARTAGENT le meme protocole WS -> un seul connecteur parametre par
`market`. Particularites notables :

- **Token WS a bootstrapper** : pas d'URL WS fixe. On POST `/api/v1/bullet-public`
  (public, sans auth) -> {token, instanceServers:[{endpoint, pingInterval}]}. On se
  connecte ensuite a `wss://<endpoint>?token=<token>&connectId=<uuid>`.
- **Ping APPLICATIF** : le client DOIT envoyer `{"id":..,"type":"ping"}` (<pingInterval
  ~18s) sinon la connexion est fermee ; le serveur repond `{"type":"pong"}`.
- **Timestamps en NANOSECONDES** -> convertis en ms (to_ms).
- **Tailles Futures en CONTRATS** -> multipliees par le `multiplier` du contrat pour
  obtenir la quantite en actif de base (XBTUSDTM = 0.001 BTC, ETHUSDTM = 0.01 ETH).
- **Symboles** : Spot BTC-USDT (BTCUSDT <-> BTC-USDT) ; Futures XBTUSDTM/ETHUSDTM
  (Bitcoin = XBT chez KuCoin Futures ; BTCUSDT <-> XBTUSDTM, ETHUSDT <-> ETHUSDTM).
- **Carnet** : canal `level2Depth50` (push du TOP-50 complet ~100ms, REMPLACEMENT
  TOTAL) -> pas de sequence ni de resync (chaque push est un carnet frais). Uniforme
  spot/futures. Compromis : 50 niveaux (cf. docs/kucoin.md ; le canal incrementiel
  `level2` donnerait plus de profondeur mais formats spot/futures divergents + snapshot
  REST partiel -> fragile). `side` trade = cote du TAKER = AGRESSEUR (pas d'inversion).

Docs : docs/kucoin.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
import uuid

import websockets

from ..models import OrderBook, Trade, now_ms
from .base import BaseConnector

log = logging.getLogger("connector.kucoin")

SPOT_HOST = "https://api.kucoin.com"
FUT_HOST = "https://api-futures.kucoin.com"
BULLET_PATH = "/api/v1/bullet-public"

# KuCoin Futures : quantite en actif de base = nb de contrats x multiplier.
# Valeurs confirmees en live le 2026-06-27 (GET /api/v1/contracts/active).
CONTRACT_MULT: dict[str, float] = {"XBTUSDTM": 0.001, "ETHUSDTM": 0.01}

# Bitcoin = XBT chez KuCoin Futures (le spot utilise BTC).
_FUT_BASE = {"BTC": "XBT"}


def spot_symbol(sym: str) -> str:
    """BTCUSDT -> BTC-USDT (KuCoin Spot = BASE-QUOTE). Nos symboles sont *USDT."""
    return f"{sym[:-4]}-{sym[-4:]}"


def fut_symbol(sym: str) -> str:
    """BTCUSDT -> XBTUSDTM (perp USDT). Bitcoin = XBT ; suffixe M."""
    base, quote = sym[:-4], sym[-4:]
    return f"{_FUT_BASE.get(base, base)}{quote}M"


def market_symbol(market: str, sym: str) -> str:
    return fut_symbol(sym) if market == "FUTURES" else spot_symbol(sym)


def to_ms(val) -> int:
    """Horodatage KuCoin -> epoch ms. Les flux donnent des NANOSECONDES (~1.7e18) ;
    certains champs sont deja en ms (~1.7e12). On divise seulement si c'est des ns."""
    try:
        v = int(val)
    except (TypeError, ValueError):
        return now_ms()
    if v <= 0:
        return now_ms()
    return v // 1_000_000 if v > 1_000_000_000_000_000 else v   # > 1e15 = ns


def _fetch_bullet(market: str) -> tuple[str, str, float]:
    """POST bullet-public -> (token, endpoint, ping_interval_s). Synchrone (urllib) :
    appele via run_in_executor pour ne pas bloquer la boucle asyncio partagee."""
    host = FUT_HOST if market == "FUTURES" else SPOT_HOST
    req = urllib.request.Request(
        f"{host}{BULLET_PATH}", data=b"", method="POST",
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        payload = json.load(r)
    data = payload.get("data") or {}
    server = (data.get("instanceServers") or [{}])[0]
    token = data.get("token", "")
    endpoint = server.get("endpoint", "")
    ping_ms = float(server.get("pingInterval", 18000))
    if not token or not endpoint:
        raise RuntimeError(f"bullet-public invalide: {payload.get('code')}")
    return token, endpoint, ping_ms / 1000.0


class KucoinConnector(BaseConnector):
    name = "kucoin"

    # market : "FUTURES" (perp USDT) ou "SPOT". feed_name : cle du flux dans le hub.
    def __init__(self, hub, symbols, depth: int = 150,
                 market: str = "FUTURES", feed_name: str | None = None) -> None:
        super().__init__(hub, symbols, depth)
        self.market = market
        if feed_name:
            self.name = feed_name
        self._msym = {s: market_symbol(market, s) for s in symbols}   # commun -> KuCoin
        self._sym = {v: k for k, v in self._msym.items()}             # KuCoin -> commun
        # Multiplicateur de contrat par symbole KuCoin (1.0 en spot).
        self._mult = {self._msym[s]: (CONTRACT_MULT.get(self._msym[s], 1.0)
                                      if market == "FUTURES" else 1.0) for s in symbols}
        self._last_msg = 0.0
        self._last_trade = 0.0
        self._last_book = 0.0
        self._ping_s = 15.0

    def _topics(self) -> list[str]:
        syms = ",".join(self._msym.values())
        if self.market == "FUTURES":
            return [f"/contractMarket/level2Depth50:{syms}",
                    f"/contractMarket/execution:{syms}"]
        return [f"/spotMarket/level2Depth50:{syms}",
                f"/market/match:{syms}"]

    async def _stream(self) -> None:
        now = time.monotonic()
        self._last_msg = now
        self._last_trade = now
        self._last_book = now
        loop = asyncio.get_running_loop()
        token, endpoint, ping_s = await loop.run_in_executor(None, _fetch_bullet, self.market)
        self._ping_s = min(max(ping_s * 0.6, 8.0), 18.0)   # ping bien avant l'echeance
        url = f"{endpoint}?token={token}&connectId={uuid.uuid4().hex}"
        # ping_interval=None : KuCoin a son propre ping applicatif (JSON).
        async with websockets.connect(url, ping_interval=None, max_size=2**22) as ws:
            for i, topic in enumerate(self._topics()):
                await ws.send(json.dumps({"id": str(int(time.time() * 1000) + i),
                                          "type": "subscribe", "topic": topic,
                                          "privateChannel": False, "response": True}))
            log.info("[%s] souscrit a %s (%s)", self.name, list(self._msym.values()), self.market)
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
        """KuCoin : envoyer {"type":"ping"} (<pingInterval) sinon la connexion ferme."""
        while True:
            await asyncio.sleep(self._ping_s)
            await ws.send(json.dumps({"id": str(int(time.time() * 1000)), "type": "ping"}))

    async def _watchdog(self, ws) -> None:
        """Reconnexion forcee si : aucun message 30s, aucun trade 120s, CARNET fige 45s
        (push level2 stoppe alors que les trades continuent), ou session > 30 min."""
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
            log.warning("[%s] erreur: %s", self.name, msg.get("data") or msg)
            return
        if mtype != "message":                       # welcome / ack / pong
            return
        topic = msg.get("topic") or ""
        ksym = topic.rsplit(":", 1)[-1]              # KuCoin symbol apres ":"
        symbol = self._sym.get(ksym)
        data = msg.get("data")
        if symbol is None or not data:
            return
        if "level2Depth50" in topic:
            self._on_book(symbol, ksym, data)
        elif topic.startswith("/market/match:") or topic.startswith("/contractMarket/execution:"):
            self._on_trade(symbol, ksym, data)

    def _on_book(self, symbol: str, ksym: str, d: dict) -> None:
        mult = self._mult.get(ksym, 1.0)
        try:
            bids = [(float(p), float(s) * mult) for p, s in (d.get("bids") or [])[: self.depth]]
            asks = [(float(p), float(s) * mult) for p, s in (d.get("asks") or [])[: self.depth]]
        except (TypeError, ValueError):
            return
        if not bids or not asks:
            return
        ts = to_ms(d.get("timestamp") or d.get("ts"))
        self.hub.set_orderbook(OrderBook(self.name, symbol, bids, asks, ts, synced=True))
        self._last_book = time.monotonic()

    def _on_trade(self, symbol: str, ksym: str, d: dict) -> None:
        mult = self._mult.get(ksym, 1.0)
        try:
            trade = Trade(
                exchange=self.name,
                symbol=symbol,
                price=float(d["price"]),
                size=float(d["size"]) * mult,          # contrats -> actif de base (1.0 en spot)
                side="buy" if d.get("side") == "buy" else "sell",   # taker = agresseur
                ts=to_ms(d.get("time") or d.get("ts")),
                trade_id=str(d.get("tradeId", "")),
            )
        except (KeyError, ValueError):
            return
        self._last_trade = time.monotonic()
        self.hub.add_trades(self.name, symbol, [trade])
