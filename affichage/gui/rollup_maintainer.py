"""Maintient le rollup du footprint + applique la retention (thread de fond).

Deux roles, hors thread Qt :
- DRAINE en continu `TradeArchive.rollup_step()` : agrege les nouveaux trades
  (live, gap-fill, backfill) dans le rollup pre-agrege, par paquets bornes.
- PURGE periodiquement (retention) les trades bruts deja agreges + les snapshots
  de carnet plus vieux que la fenetre de retention -> borne la taille disque.

Le rollup rend le footprint zoom arriere quasi instantane (lecture proportionnelle
a l'affichage, pas au nombre de trades) ; la retention empeche trades.db/books.db
de croitre sans fin.
"""
from __future__ import annotations

import logging
import threading
import time

from backend.archive import TradeArchive
from backend.book_archive import BookArchive

log = logging.getLogger("rollup")

RETENTION_DAYS = 7          # on garde le DETAIL tick (trades bruts) ce nombre de jours
# Horizon DUR du rollup (footprint pre-agrege) : bien plus long que les trades bruts
# (le rollup EST l'historique du footprint), mais BORNE -> sinon footprint_rollup/_ohlc
# croissent sans fin (revue #7). 1 an = navigation profonde tout en bornant le disque.
ROLLUP_RETENTION_DAYS = 365
PURGE_INTERVAL_S = 3600.0   # frequence de la purge de retention
DRAIN_BATCH = 20000         # trades agreges par appel a rollup_step


class RollupMaintainer:
    def __init__(self, archive: TradeArchive, book_archive: BookArchive | None = None,
                 retention_days: float = RETENTION_DAYS, arb_archive=None,
                 rollup_retention_days: float = ROLLUP_RETENTION_DAYS) -> None:
        self.archive = archive
        self.book_archive = book_archive
        self.arb_archive = arb_archive
        self.retention_ms = int(retention_days * 86_400_000)
        self.rollup_retention_ms = int(rollup_retention_days * 86_400_000)
        self._stop = False
        self._last_purge = 0.0
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rollup", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        self._thread.join(timeout=3.0)

    def _run(self) -> None:
        while not self._stop:
            try:
                n = self.archive.rollup_step(DRAIN_BATCH)
            except Exception as exc:  # noqa: BLE001 - I/O/SQL -> on reessaiera
                log.warning("rollup_step: %s", exc)
                n = 0
            now = time.monotonic()
            if now - self._last_purge >= PURGE_INTERVAL_S:
                self._purge()
                self._last_purge = now
            if self._stop:
                break
            if n >= DRAIN_BATCH:
                continue                       # en retard -> on draine sans attendre
            self._wake.wait(2.0)               # a jour -> repos (reveillable par stop)
            self._wake.clear()

    def _purge(self) -> None:
        now = int(time.time() * 1000)
        cutoff = now - self.retention_ms
        rollup_cutoff = now - self.rollup_retention_ms
        try:
            nt = self.archive.purge(cutoff)
            nr = self.archive.purge_rollup(rollup_cutoff)      # horizon DUR du rollup
            nb = self.book_archive.purge(cutoff) if self.book_archive else 0
            na = self.arb_archive.purge(cutoff) if self.arb_archive else 0
            if nt or nb or na or nr:
                log.info("retention : -%d trades (>%d j), -%d cellules rollup (>%d j), "
                         "-%d snapshots carnet, -%d arbitrages",
                         nt, self.retention_ms // 86_400_000,
                         nr, self.rollup_retention_ms // 86_400_000, nb, na)
        except Exception as exc:  # noqa: BLE001
            log.warning("purge retention: %s", exc)
