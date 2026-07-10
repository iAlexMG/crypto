"""Service des connecteurs : boucle asyncio (WebSockets) dans un thread de fond.

IMPORTANT : TOUS les flux (chaque exchange × chaque marché) sont captés EN
PERMANENCE et EN MÊME TEMPS, chacun sous sa propre cle de flux dans le hub.
Changer d'exchange ou de marche dans l'interface ne fait que changer la cle LUE
par la vue : aucun connecteur n'est arrete, aucune donnee n'est perdue.

Ajouter un exchange = un connecteur + une (ou deux) entree(s) dans FEEDS + une
fiche docs/<exchange>.md.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Callable

from backend.hub import MarketHub
from backend.models import SYMBOLS, Trade
from backend.connectors.base import BaseConnector
from backend.connectors.bitget import BitgetConnector
from backend.connectors.bitget_rest import fetch_since as _bitget_fetch
from backend.connectors.bitget_rest import fetch_before as _bitget_fetch_before
from backend.connectors.binance import BinanceConnector
from backend.connectors.binance_rest import fetch_since as _binance_fetch
from backend.connectors.binance_rest import fetch_before as _binance_fetch_before
from backend.connectors.binance_rest import fetch_range as _binance_fetch_range
from backend.connectors.bybit import BybitConnector
from backend.connectors.bybit_rest import fetch_since as _bybit_fetch
from backend.connectors.bybit_rest import fetch_before as _bybit_fetch_before
from backend.connectors.okx import OkxConnector
from backend.connectors.okx_rest import fetch_since as _okx_fetch
from backend.connectors.okx_rest import fetch_before as _okx_fetch_before
from backend.connectors.coinbase import CoinbaseConnector
from backend.connectors.coinbase_rest import fetch_since as _coinbase_fetch
from backend.connectors.coinbase_rest import fetch_before as _coinbase_fetch_before
from backend.connectors.kucoin import KucoinConnector
from backend.connectors.kucoin_rest import fetch_since as _kucoin_fetch
from backend.connectors.kucoin_rest import fetch_before as _kucoin_fetch_before


@dataclass(frozen=True)
class Feed:
    key: str          # cle de flux dans le hub (= Trade.exchange) + identifiant unique
    exchange: str     # libelle d'exchange ("Bitget", "Binance")
    market: str       # "FUTURES" | "SPOT"
    market_tag: str   # tag de la colonne `market` de l'archive (unique par flux)


# Registry des flux captes. L'ordre fixe l'ordre d'affichage des boutons.
FEEDS: list[Feed] = [
    Feed("bitget",       "Bitget",  "FUTURES", "USDT-FUTURES"),
    Feed("bitget_spot",  "Bitget",  "SPOT",    "SPOT"),
    Feed("binance",      "Binance", "FUTURES", "binance-futures"),
    Feed("binance_spot", "Binance", "SPOT",    "binance-spot"),
    Feed("bybit",        "Bybit",   "FUTURES", "bybit-linear"),
    Feed("bybit_spot",   "Bybit",   "SPOT",    "bybit-spot"),
    Feed("okx",          "OKX",     "FUTURES", "okx-swap"),
    Feed("okx_spot",     "OKX",     "SPOT",    "okx-spot"),
    # Coinbase : SPOT uniquement (l'API publique Exchange ne couvre pas les futures).
    Feed("coinbase_spot", "Coinbase", "SPOT",  "coinbase-spot"),
    # KuCoin : Spot + Futures (perps USDT XBTUSDTM/ETHUSDTM). Historique REST recent seul.
    Feed("kucoin",       "KuCoin",   "FUTURES", "kucoin-futures"),
    Feed("kucoin_spot",  "KuCoin",   "SPOT",    "kucoin-spot"),
]


def _bybit_category(market: str) -> str:
    return "linear" if market == "FUTURES" else "spot"

EXCHANGES: list[str] = list(dict.fromkeys(f.exchange for f in FEEDS))
MARKETS: list[str] = list(dict.fromkeys(f.market for f in FEEDS))
DEFAULT_EXCHANGE = "Bitget"
DEFAULT_MARKET = "FUTURES"

# --- Vue HYBRIDE (agregation recalee sur Bitget) -----------------------------
# Entree "exchange" virtuelle au menu : agregation de plusieurs flux du MEME marche,
# recales sur l'axe de prix Bitget (cf. docs/hybride.md). On ajoute les exchanges UN
# PAR UN (validation du recalage par etape). Bitget = reference (spread 0). Coinbase
# est EXCLU (pas de futures + paires USDT peu liquides).
HYBRID_LABEL = "Hybride"
HYBRID_EXCHANGES: list[str] = ["Bitget", "Binance", "Bybit", "OKX", "KuCoin"]   # ordre d'agregation ; grandit par etape


def hybrid_feeds(market: str) -> list[Feed]:
    """Flux agreges par l'hybride pour ce marche (ordre HYBRID_EXCHANGES). Le 1er
    (Bitget) est la reference de prix."""
    out: list[Feed] = []
    for ex in HYBRID_EXCHANGES:
        for f in FEEDS:
            if f.exchange == ex and f.market == market:
                out.append(f)
    return out


def hybrid_base(market: str) -> Feed:
    """Flux de reference (Bitget) du marche -> axe de prix de l'hybride."""
    return feed_for("Bitget", market)


def feed_for(exchange: str, market: str) -> Feed:
    for f in FEEDS:
        if f.exchange == exchange and f.market == market:
            return f
    raise KeyError(f"flux inconnu: {exchange}/{market}")


def markets_for(exchange: str) -> list[str]:
    """Marches DISPONIBLES pour un exchange (ordre des MARKETS). Tous n'offrent pas
    les deux : Coinbase = SPOT seulement -> la GUI s'y adapte au lieu de planter."""
    avail = {f.market for f in FEEDS if f.exchange == exchange}
    return [m for m in MARKETS if m in avail]


def _make_connector(feed: Feed, hub: MarketHub) -> BaseConnector:
    if feed.exchange == "Bitget":
        return BitgetConnector(hub, list(SYMBOLS), depth=150,
                               inst_type=feed.market_tag, feed_name=feed.key)
    if feed.exchange == "Bybit":
        return BybitConnector(hub, list(SYMBOLS), depth=150,
                              market=feed.market, feed_name=feed.key)
    if feed.exchange == "OKX":
        return OkxConnector(hub, list(SYMBOLS), depth=150,
                            market=feed.market, feed_name=feed.key)
    if feed.exchange == "Coinbase":
        return CoinbaseConnector(hub, list(SYMBOLS), depth=150,
                                 market=feed.market, feed_name=feed.key)
    if feed.exchange == "KuCoin":
        return KucoinConnector(hub, list(SYMBOLS), depth=150,
                               market=feed.market, feed_name=feed.key)
    return BinanceConnector(hub, list(SYMBOLS), depth=150,
                            market=feed.market, feed_name=feed.key)


def fetch_for(feed: Feed) -> Callable[[str, int], list[Trade]]:
    """Renvoie la fonction REST d'historique des trades pour ce flux (vers
    l'avant, a partir d'un ts) -> utilisee par le backfill du present."""
    if feed.exchange == "Bitget":
        inst = feed.market_tag
        return lambda sym, start: _bitget_fetch(inst, sym, start)
    if feed.exchange == "Bybit":
        cat = _bybit_category(feed.market)
        return lambda sym, start: _bybit_fetch(cat, sym, start)
    if feed.exchange == "OKX":
        market = feed.market
        return lambda sym, start: _okx_fetch(market, sym, start)
    if feed.exchange == "Coinbase":
        return lambda sym, start: _coinbase_fetch(sym, start)
    if feed.exchange == "KuCoin":
        market = feed.market
        return lambda sym, start: _kucoin_fetch(market, sym, start)
    market = feed.market
    return lambda sym, start: _binance_fetch(market, sym, start)


def fetch_before_for(feed: Feed) -> Callable[[str, "str | None"], list[Trade]]:
    """Renvoie la fonction REST qui REMONTE le passe pour ce flux (un paquet de
    trades plus anciens que `before_id`) -> utilisee par le collecteur. Meme
    interface pour tous les exchanges : le collecteur reste agnostique."""
    if feed.exchange == "Bitget":
        inst = feed.market_tag
        return lambda sym, before: _bitget_fetch_before(inst, sym, before)
    if feed.exchange == "Bybit":
        cat = _bybit_category(feed.market)
        return lambda sym, before: _bybit_fetch_before(cat, sym, before)
    if feed.exchange == "OKX":
        market = feed.market
        return lambda sym, before: _okx_fetch_before(market, sym, before)
    if feed.exchange == "Coinbase":
        return lambda sym, before: _coinbase_fetch_before(sym, before)
    if feed.exchange == "KuCoin":
        market = feed.market
        return lambda sym, before: _kucoin_fetch_before(market, sym, before)
    market = feed.market
    return lambda sym, before: _binance_fetch_before(market, sym, before)


def fetch_range_for(feed: Feed):
    """Renvoie la fonction de SEEK DIRECT par temps (combler un trou [a,b] sans
    paginer depuis le present), ou None si l'exchange ne supporte pas le seek par
    startTime. Binance : oui (aggTrades startTime). Bitget : non (Fills-History =
    idLessThan seulement) -> None, le collecteur garde fetch_before pour Bitget.
    Bybit : pas d'historique profond du tout (recent-trade) -> None aussi.
    OKX : historique profond OUI, mais pas de seek par temps utilise ici -> None
    (le comblage amorce fetch_before au bord du trou, suffisant).
    Coinbase : idem OKX (pagination par tradeId seulement) -> None.
    KuCoin : pas d'historique profond (recent-trade, comme Bybit) -> None."""
    if feed.exchange in ("Bitget", "Bybit", "OKX", "Coinbase", "KuCoin"):
        return None
    market = feed.market
    return lambda sym, a_ms, b_ms: _binance_fetch_range(market, sym, a_ms, b_ms)


class ConnectorService:
    def __init__(self) -> None:
        self.hub = MarketHub(exchanges=[f.key for f in FEEDS], symbols=SYMBOLS)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="connectors", daemon=True)
        self._connectors: list[BaseConnector] = []
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._spawn)
        self._loop.run_forever()
        self._loop.close()

    def _spawn(self) -> None:
        for feed in FEEDS:
            conn = _make_connector(feed, self.hub)
            self._connectors.append(conn)
            self._tasks.append(asyncio.create_task(conn.run()))

    def stop(self) -> None:
        async def _shutdown() -> None:
            for c in self._connectors:
                c.stop()
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._loop.stop()
        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
