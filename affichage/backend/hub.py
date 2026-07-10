"""MarketHub : etat de marche centralise, EN MEMOIRE uniquement.

Aucune persistance / aucun historique sur disque. On ne garde que :
- l'etat courant complet de chaque orderbook (par exchange, par symbole),
- un buffer borne (fenetre glissante) des derniers trades pour l'orderflow.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Iterable

from .models import OrderBook, Trade

TRADES_BUFFER = 60000  # nb max de trades conserves par (exchange, symbole)


class MarketHub:
    """Etat de marche partage entre le thread des connecteurs (ecriture) et le
    thread d'affichage (lecture). Protege par un verrou."""

    def __init__(self, exchanges: Iterable[str], symbols: Iterable[str]) -> None:
        self.exchanges: list[str] = list(exchanges)
        self.symbols: list[str] = list(symbols)
        self._lock = threading.Lock()
        # etat courant
        self._books: dict[tuple[str, str], OrderBook] = {}
        self._trades: dict[tuple[str, str], deque[Trade]] = {}
        for ex in self.exchanges:
            for sym in self.symbols:
                self._books[(ex, sym)] = OrderBook(exchange=ex, symbol=sym)
                self._trades[(ex, sym)] = deque(maxlen=TRADES_BUFFER)

    # --- ecriture (appelee par les connecteurs) ---------------------------
    def set_orderbook(self, book: OrderBook) -> None:
        with self._lock:
            self._books[(book.exchange, book.symbol)] = book

    def add_trades(self, exchange: str, symbol: str, trades: list[Trade]) -> None:
        with self._lock:
            buf = self._trades.get((exchange, symbol))
            if buf is not None:
                buf.extend(trades)

    def clear(self) -> None:
        """Reinitialise tout l'etat (ex. lors d'un changement SPOT/Futures)."""
        with self._lock:
            for ex in self.exchanges:
                for sym in self.symbols:
                    self._books[(ex, sym)] = OrderBook(exchange=ex, symbol=sym)
                    self._trades[(ex, sym)].clear()

    # --- lecture ----------------------------------------------------------
    def book(self, exchange: str, symbol: str) -> OrderBook:
        with self._lock:
            return self._books[(exchange, symbol)]

    def trades(self, exchange: str, symbol: str) -> list[Trade]:
        with self._lock:
            return list(self._trades.get((exchange, symbol), ()))

    def trades_since(self, exchange: str, symbol: str, since_ms: int) -> list[Trade]:
        """Trades avec ts >= since_ms. Parcourt seulement la queue (du plus
        recent au plus ancien) -> O(fenetre visible), pas O(tout le buffer)."""
        buf = self._trades.get((exchange, symbol))
        if buf is None:
            return []
        out: list[Trade] = []
        with self._lock:
            for t in reversed(buf):
                if t.ts < since_ms:
                    break
                out.append(t)
        out.reverse()
        return out

    def last_trade_ts(self, exchange: str, symbol: str) -> int | None:
        """ts (ms) du dernier trade reçu, ou None. Léger (pas de copie) -> sert
        au log de santé périodique pour repérer un canal trade figé."""
        buf = self._trades.get((exchange, symbol))
        with self._lock:
            return buf[-1].ts if buf else None

    def books_for(self, symbol: str) -> list[OrderBook]:
        with self._lock:
            return [self._books[(ex, symbol)] for ex in self.exchanges]
