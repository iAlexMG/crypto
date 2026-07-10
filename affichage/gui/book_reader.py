"""Lecteur ASYNCHRONE de l'historique de carnet (books.db) pour la heatmap.

Deux garde-fous contre le gel quand books.db devient enorme (sessions longues,
plusieurs exchanges) :

1. LECTURE BORNEE : on ne demande JAMAIS toute une plage brute, mais ~max_cols
   colonnes reparties (BookArchive.query_sampled). La heatmap n'affiche de toute
   facon pas plus de MAX_RENDER_COLS colonnes -> sortir plus serait gaspille.
2. HORS THREAD Qt : la requete disque tourne dans un thread de fond. Le thread
   d'affichage ne bloque jamais sur SQLite ; il recupere le dernier resultat pret
   (ou rien, le temps que ca charge -> la heatmap se complete une fraction de
   seconde apres, sans freeze).

Le resultat est convertit en `history.Column` -> directement utilisable par
_draw_heatmap / _draw_bbo, comme l'historique en memoire.
"""
from __future__ import annotations

import logging
import threading

from backend.book_archive import BookArchive
from .history import Column, MAX_RENDER_COLS

log = logging.getLogger("book_reader")

# cle de requete : (market, symbol, t0_ms, t1_ms) arrondie a la seconde pour ne
# pas relancer une lecture a chaque micro-variation de la fenetre.
Key = tuple


class BookReader:
    def __init__(self, archive: BookArchive, max_cols: int = MAX_RENDER_COLS,
                 cache_size: int = 8) -> None:
        self.archive = archive
        self.max_cols = max_cols
        self.cache_size = cache_size
        self._want: Key | None = None
        self._result: dict[Key, list[Column]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="book_reader",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        # joindre avant la fermeture de l'archive (cf. FootprintReader) -> pas de
        # lecture sur une connexion deja fermee au shutdown.
        self._stop = True
        self._wake.set()
        self._thread.join(timeout=2.0)

    @staticmethod
    def key(market: str, symbol: str, t0_ms: int, t1_ms: int) -> Key:
        return (market, symbol, t0_ms // 1000 * 1000, t1_ms // 1000 * 1000)

    def request(self, key: Key) -> None:
        """Demande (non bloquante) le chargement d'une fenetre. Ignore si deja en
        cache ou deja demandee."""
        with self._lock:
            if key == self._want or key in self._result:
                return
            self._want = key
        self._wake.set()

    def get(self, key: Key) -> list[Column]:
        """Renvoie le resultat si pret, sinon liste vide (sans bloquer)."""
        with self._lock:
            return self._result.get(key, [])

    def _run(self) -> None:
        while not self._stop:
            self._wake.wait()
            self._wake.clear()
            if self._stop:
                break
            with self._lock:
                key = self._want
            if key is None or key in self._result:
                continue
            market, symbol, t0_ms, t1_ms = key
            try:
                snaps = self.archive.query_sampled(market, symbol, t0_ms, t1_ms,
                                                   self.max_cols)
            except Exception as exc:  # noqa: BLE001 - I/O disque -> resultat vide
                log.warning("book read %s %s: %s", market, symbol, exc)
                snaps = []
            cols = [Column(s.ts / 1000.0, s.prices, s.sizes, s.bid, s.ask)
                    for s in snaps]
            with self._lock:
                self._result[key] = cols
                # cache borne : on ne garde que les dernieres fenetres consultees
                while len(self._result) > self.cache_size:
                    self._result.pop(next(iter(self._result)))
            # une autre fenetre a pu etre demandee entre-temps -> reboucler
            self._wake.set()
