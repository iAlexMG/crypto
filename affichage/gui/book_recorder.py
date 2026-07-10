"""Enregistreur de snapshots de carnet (thread de fond) -> books.db.

Persiste le carnet de TOUS les flux (exchange × marche) a cadence reduite, en
continu, pour constituer un historique de heatmap (le carnet n'a pas d'historique
REST). Tourne dans son propre thread : l'ecriture disque ne touche jamais le
thread Qt (pas de hitch a 10 fps).

Cadence DELIBEREMENT plus basse que l'echantillonnage memoire de la heatmap
(gui/history.py, 2 Hz) : 1 Hz suffit a un historique Bookmap et borne la taille
de books.db. Constante `BOOK_PERSIST_MS` ajustable.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from backend.book_archive import BookArchive
from backend.hub import MarketHub

log = logging.getLogger("book_recorder")

BOOK_PERSIST_MS = 1000   # 1 Hz : 1 snapshot de carnet persiste par seconde et par flux


class BookRecorder:
    def __init__(self, archive: BookArchive, hub: MarketHub,
                 feeds: list[tuple[str, str]], symbols: list[str],
                 period_ms: int = BOOK_PERSIST_MS) -> None:
        self.archive = archive
        self.hub = hub
        self.feeds = list(feeds)            # (cle hub, tag archive)
        self.symbols = list(symbols)
        self.period = period_ms / 1000.0
        self._last_ts: dict[tuple[str, str], int] = {}   # dernier book.ts persiste
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="book_recorder",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def _run(self) -> None:
        while not self._stop:
            t0 = time.monotonic()
            self._tick()
            # cadence reguliere quelle que soit la duree des ecritures
            time.sleep(max(0.0, self.period - (time.monotonic() - t0)))

    def _tick(self) -> None:
        for key, tag in self.feeds:
            for sym in self.symbols:
                book = self.hub.book(key, sym)
                levels = book.bids + book.asks
                if not levels or not book.ts:
                    continue
                fk = (key, sym)
                # le carnet n'a pas forcement bouge depuis le dernier tour :
                # inutile de reecrire le meme ts (la PK l'ignorerait de toute
                # facon, mais on evite l'I/O).
                if self._last_ts.get(fk) == book.ts:
                    continue
                prices = np.fromiter((p for p, _ in levels), np.float64, len(levels))
                sizes = np.fromiter((s for _, s in levels), np.float32, len(levels))
                try:
                    self.archive.insert_snapshot(
                        tag, sym, int(book.ts),
                        book.best_bid or 0.0, book.best_ask or 0.0, prices, sizes)
                    self._last_ts[fk] = book.ts
                except Exception as exc:  # noqa: BLE001 - I/O disque -> on reessaiera
                    log.warning("book persist echec %s %s: %s", key, sym, exc)
