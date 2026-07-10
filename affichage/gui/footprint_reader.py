"""Lecteur ASYNCHRONE du footprint historique (agrege en SQL) pour le zoom arriere.

En zoom arriere, reconstruire le footprint depuis les trades BRUTS est ruineux
(charger + iterer des dizaines/centaines de milliers de trades a chaque image ->
~300 ms/frame, gel + GIL sature). Ici on agrege EN SQL (archive.aggregate_footprint)
dans un thread de fond : le resultat est BORNE (bougies × niveaux) et l'I/O ne
touche jamais le thread Qt. La vue recupere les dernieres bougies pretes (ou rien,
le temps que ca calcule -> elles apparaissent une fraction de seconde apres).

Bornes le scan en se limitant a l'archive [t0, t1] ; la pointe live (derniers ~2 s
pas encore archivee) est invisible a cette echelle de zoom.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from backend.archive import TradeArchive
from .candles import Candle, build_candles_agg

log = logging.getLogger("footprint_reader")

Key = tuple

# Au-dela de cette plage, on NE reconstruit PAS les lignes bid/ask FINES depuis les
# trades bruts (scan trop large) -> repli sur le pas 60 s du rollup (bougies). En
# deca, lignes fines (plage/N -> ~1 s a quelques s) sans gel (calcul async + borne).
FINE_BBO_MAX_SPAN_MS = 24 * 3600 * 1000  # 24 h (scan SQL borne en sortie, GIL-friendly)
FINE_BBO_MAX_POINTS = 1000               # nb de buckets cible (borne le rendu)


def _build_bbo(ask_rows, bid_rows, res_ms):
    """Assemble (ts_s, bid, ask) depuis les lignes SQL [(bucket, _, px)] : union des
    buckets des deux cotes, report (carry-forward) tant qu'un cote n'a pas re-trade,
    NaN avant le 1er trade du cote. ts = milieu du bucket (s)."""
    asks = {b: px for b, _, px in ask_rows}
    bids = {b: px for b, _, px in bid_rows}
    buckets = sorted(set(asks) | set(bids))
    n = len(buckets)
    if n == 0:
        e = np.empty(0, np.float64)
        return e, e, e
    ts = np.empty(n, np.float64); bid = np.empty(n, np.float64); ask = np.empty(n, np.float64)
    lb = la = np.nan
    for i, b in enumerate(buckets):
        if b in bids:
            lb = bids[b]
        if b in asks:
            la = asks[b]
        ts[i] = (b + 0.5) * res_ms / 1000.0
        bid[i] = lb; ask[i] = la
    return ts, bid, ask


class FootprintReader:
    def __init__(self, archive: TradeArchive, cache_size: int = 6) -> None:
        self.archive = archive
        self.cache_size = cache_size
        self._want: Key | None = None
        # par fenetre : les bougies agregees (footprint). Les scatters ne viennent
        # plus d'ici : ils sont limites a la derniere heure (buffer live).
        self._result: dict[Key, list[Candle]] = {}
        # par fenetre : la serie bid/ask FINE (ts, bid, ask) ou None (plage trop large)
        self._bbo: dict[Key, tuple | None] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="footprint_reader",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        # JOINDRE le thread avant que l'appelant ne ferme l'archive : sinon un scan
        # en cours opererait sur une connexion fermee (warning "closed database").
        self._stop = True
        self._wake.set()
        self._thread.join(timeout=2.0)

    @staticmethod
    def key(market: str, symbol: str, t0_ms: int, t1_ms: int,
            res_s: float, tick: float) -> Key:
        # arrondis -> on ne relance pas un calcul a chaque micro-variation
        return (market, symbol, t0_ms // 1000 * 1000, t1_ms // 1000 * 1000,
                round(res_s, 3), round(tick, 6))

    def request(self, key: Key) -> None:
        with self._lock:
            if key == self._want or key in self._result:
                return
            self._want = key
        self._wake.set()

    def get(self, key: Key) -> list[Candle] | None:
        with self._lock:
            return self._result.get(key)

    def get_bbo(self, key: Key):
        """Serie bid/ask FINE (ts, bid, ask) pour cette fenetre, ou None si la plage
        depasse FINE_BBO_MAX_SPAN_MS (repli sur le pas 60 s cote GUI)."""
        with self._lock:
            return self._bbo.get(key)

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
            market, symbol, t0_ms, t1_ms, res_s, tick = key
            try:
                # lecture DEPUIS LE ROLLUP pre-agrege (repli auto sur le brut si la
                # fenetre n'est pas encore couverte) -> cout borne par l'affichage.
                # market = tuple (base_tag, feed_tags) -> footprint HYBRIDE (somme
                # recalee de plusieurs marches) ; sinon = un seul marche.
                if isinstance(market, tuple):
                    # market = (base_tag, feed_tags, base_vol[, basis_token]) ; le token
                    # porte le basis trade-matché FIGE du live ({tag: ((cb, off), ...)}) ->
                    # meme recalage que le live a la couture AGG_SPAN_S (revue #6).
                    base_tag, feed_tags, base_vol = market[0], market[1], market[2]
                    basis_by_feed = None
                    if len(market) >= 4 and market[3]:
                        basis_by_feed = {tag: dict(items) for tag, items in market[3]}
                    vol, opn, cls, ask, bid = self.archive.aggregate_footprint_rollup_hybrid(
                        base_tag, feed_tags, symbol, t0_ms, t1_ms, res_s, tick, base_vol,
                        basis_by_feed)
                else:
                    vol, opn, cls, ask, bid = self.archive.aggregate_footprint_rollup(
                        market, symbol, t0_ms, t1_ms, res_s, tick)
                candles = build_candles_agg(vol, opn, cls, ask, bid, res_s, tick)
            except Exception as exc:  # noqa: BLE001 - I/O/SQL -> resultat vide
                log.warning("footprint read %s %s: %s", market, symbol, exc)
                candles = []
            # PUBLIE le footprint TOUT DE SUITE (cheap, depuis le rollup), avant le
            # calcul bid/ask (scan trades bruts, plus lent en grande plage) -> le
            # footprint n'attend pas la serie de lignes.
            with self._lock:
                self._result[key] = candles
                while len(self._result) > self.cache_size:
                    k = next(iter(self._result))
                    self._result.pop(k)
                    self._bbo.pop(k, None)
            self._wake.set()
            # lignes bid/ask FINES depuis les trades bruts (plage/N buckets), SAUF
            # si la plage est trop large -> None (la GUI retombe sur le pas 60 s).
            bbo = None
            try:
                span_ms = t1_ms - t0_ms
                # lignes bid/ask hybrides = celles de la REFERENCE (Bitget) -> base_tag.
                bbo_market = market[0] if isinstance(market, tuple) else market
                if 0 < span_ms <= FINE_BBO_MAX_SPAN_MS:
                    res_ms = max(1000, span_ms // FINE_BBO_MAX_POINTS)
                    a_rows, b_rows = self.archive.bbo_series(
                        bbo_market, symbol, t0_ms, t1_ms, res_ms)
                    series = _build_bbo(a_rows, b_rows, res_ms)
                    bbo = series if series[0].size else None
            except Exception as exc:  # noqa: BLE001 - I/O/SQL -> pas de serie fine
                log.warning("bbo read %s %s: %s", market, symbol, exc)
            with self._lock:
                if key in self._result:          # pas evince entre-temps
                    self._bbo[key] = bbo
            self._wake.set()
