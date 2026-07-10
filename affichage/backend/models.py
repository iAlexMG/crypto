"""Schema de donnees unifie (format de reference : Bitget).

Tous les connecteurs normalisent leurs donnees vers ces structures.
- Symboles  : format Bitget spot, ex. "BTCUSDT", "ETHUSDT".
- Prix/size : floats, unite = quantite en actif de base (BTC, ETH).
- Timestamp : epoch milliseconds UTC.
- Side      : "buy" / "sell" = cote de l'agresseur du trade.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Side = Literal["buy", "sell"]

# Paires supportees (format unifie Bitget). Une seule affichee a la fois cote UI.
SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class Trade:
    """Un trade execute, normalise."""
    exchange: str
    symbol: str
    price: float
    size: float
    side: Side          # cote de l'agresseur
    ts: int             # epoch ms UTC
    trade_id: str = ""

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "price": self.price,
            "size": self.size,
            "side": self.side,
            "ts": self.ts,
            "id": self.trade_id,
        }


@dataclass(slots=True)
class OrderBook:
    """Carnet d'ordres normalise, deja tronque a la profondeur d'affichage.

    bids : tries par prix decroissant (meilleur bid en premier).
    asks : tries par prix croissant  (meilleur ask en premier).
    Chaque niveau est un tuple (price, size).
    """
    exchange: str
    symbol: str
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    ts: int = 0
    synced: bool = False   # True quand le carnet est valide (checksum/sequence OK)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        return None

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "bids": self.bids,
            "asks": self.asks,
            "ts": self.ts,
            "synced": self.synced,
        }
