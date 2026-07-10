"""Historique d'affichage EN MEMOIRE (aucune persistance disque).

Echantillonne le carnet des DEUX paires en continu (independamment de la paire
affichee) -> on peut changer de paire et revenir sans rien perdre.

Plafonne le nombre de colonnes (MAX_COLS) : on garde l'historique depuis le
lancement jusqu'a ce plafond, puis on glisse. Au rendu, on ne renvoie qu'un
nombre borne de colonnes de la fenetre visible (sous-echantillonnage) pour que
le zoom arriere ne ralentisse jamais l'application.
"""
from __future__ import annotations

import math
import time
from collections import deque

import numpy as np

from backend.hub import MarketHub

SAMPLE_MS = 500              # periode d'echantillonnage d'une colonne (2 Hz)
MAX_COLS = 14_400           # ~2 h a 2 Hz (plafond memoire/perf)
MAX_RENDER_COLS = 1_000     # colonnes max construites par image (perf zoom out)


class Column:
    __slots__ = ("ts", "prices", "sizes", "bid", "ask")

    def __init__(self, ts: float, prices: np.ndarray, sizes: np.ndarray,
                 bid: float, ask: float) -> None:
        self.ts = ts
        self.prices = prices
        self.sizes = sizes
        self.bid = bid
        self.ask = ask


class SymbolHistory:
    def __init__(self) -> None:
        self.cols: deque[Column] = deque(maxlen=MAX_COLS)

    def add(self, ts: float, prices: np.ndarray, sizes: np.ndarray,
            bid: float, ask: float) -> None:
        self.cols.append(Column(ts, prices, sizes, bid, ask))

    def visible(self, t0: float, t1: float, max_cols: int = MAX_RENDER_COLS) -> list[Column]:
        sel = [c for c in self.cols if t0 <= c.ts <= t1]
        if len(sel) > max_cols:
            stride = math.ceil(len(sel) / max_cols)
            sel = sel[::stride]
        return sel

    @property
    def t_first(self) -> float | None:
        return self.cols[0].ts if self.cols else None

    @property
    def t_last(self) -> float | None:
        return self.cols[-1].ts if self.cols else None


class HistoryStore:
    def __init__(self, exchange: str, symbols: list[str]) -> None:
        self.exchange = exchange
        self.symbols = symbols
        self.hist: dict[str, SymbolHistory] = {s: SymbolHistory() for s in symbols}
        self._last_sample = 0.0

    def clear(self) -> None:
        for h in self.hist.values():
            h.cols.clear()

    def sample(self, hub: MarketHub) -> None:
        """Capture une colonne pour CHAQUE paire (appele par un QTimer).

        La colonne est horodatee avec l'horloge de l'EXCHANGE (book.ts), comme
        les trades -> heatmap et scatter restent alignes meme si l'horloge locale
        differe de celle de l'exchange.
        """
        now = time.time()
        if (now - self._last_sample) * 1000 < SAMPLE_MS:
            return
        self._last_sample = now
        for sym in self.symbols:
            book = hub.book(self.exchange, sym)
            levels = book.bids + book.asks
            if not levels:
                continue
            ts = (book.ts / 1000.0) if book.ts else now
            prices = np.fromiter((p for p, _ in levels), np.float64, len(levels))
            sizes = np.fromiter((s for _, s in levels), np.float32, len(levels))
            self.hist[sym].add(ts, prices, sizes,
                               book.best_bid or 0.0, book.best_ask or 0.0)
