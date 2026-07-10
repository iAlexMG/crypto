"""Gestionnaire de backfill des trades historiques (thread de fond).

Perimetre VOLONTAIREMENT LIMITE au PRESENT : le GUI ne comble que ce qui est
necessaire a l'affichage temps reel. L'historique PROFOND (navigation passee)
est rempli par le collecteur (`gui/collector.py`), qui remonte le temps en
arriere-plan ; le GUI se contente de LIRE `trades.db`.

- Prefixe de la chandelle OUVERTE : au lancement et a chaque changement de
  resolution, on charge de l'ouverture de la chandelle courante -> maintenant.
- Archivage continu : les trades live (WS) de TOUS les flux (tous exchanges ×
  marches) sont pousses dans l'archive en permanence.

(Il n'y a PLUS de backfill « a la demande » au dezoom : le pont `ensure_range`
a ete retire au profit du collecteur.)

Chaque flux fournit sa propre fonction REST (`fetch`) et son `market_tag` (colonne
`market` de la base) -> agnostique a l'exchange.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

from backend.archive import TradeArchive
from backend.hub import MarketHub

log = logging.getLogger("backfill")

INITIAL_WINDOW_MS = 30 * 60 * 1000    # fenetre de repli du 1er flush live (lookback buffer)
FLUSH_INTERVAL = 2.0                   # archivage des trades live toutes les 2s
LIVE_GAP_TOL_MS = 3000                # au-dela de ce silence live -> COUPURE (REST comble)

# Un flux pour le backfill : (cle hub, tag archive, fonction REST fetch(sym, start_ms))
FeedSpec = tuple[str, str, Callable[[str, int], list]]


def _covered(ts: int, intervals: list[list[int]]) -> bool:
    """True si ts tombe dans un intervalle [debut, fin] couvert par le live."""
    for a, b in intervals:
        if a <= ts <= b:
            return True
    return False


class BackfillManager:
    def __init__(self, archive: TradeArchive, hub: MarketHub, symbols: list[str],
                 feeds: list[FeedSpec], active_key: str, res_s: float = 60.0) -> None:
        self.archive = archive
        self.hub = hub
        self.symbols = list(symbols)
        self._feeds = {key: (tag, fetch) for key, tag, fetch in feeds}
        self.active_key = active_key        # flux AFFICHE (pour ensure_range)
        self.res_s = float(res_s)           # resolution -> taille de la chandelle ouverte
        self._loaded: dict[tuple[str, str], int] = {}    # (key, sym) -> plus ancien ms charge
        self._pending: dict[tuple[str, str], int] = {}
        self._queue: deque[tuple[str, str, int]] = deque()
        self._last_flush: dict[tuple[str, str], int] = {}   # (key, sym) -> dernier ts flush
        # Intervalles [debut, fin] (ms) reellement COUVERTS par le live, par flux.
        # Le live possede ces plages ; le REST ne les recouvre pas (anti double-
        # comptage). Une coupure live (> LIVE_GAP_TOL_MS) cree un nouvel intervalle
        # -> le creux entre deux intervalles reste comblable par le REST.
        self._live_iv: dict[tuple[str, str], list[list[int]]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="backfill", daemon=True)

    def start(self) -> None:
        self._enqueue_initial()
        self._thread.start()

    def set_active(self, key: str) -> None:
        with self._lock:
            self.active_key = key

    def set_resolution(self, res_s: float) -> None:
        """Resolution affichee changee -> recharge le prefixe de la chandelle
        ouverte (sa taille a change) pour le flux affiche."""
        self.res_s = float(res_s)
        co = self._candle_open()
        for s in self.symbols:
            self._enqueue(self.active_key, s, co)

    def _candle_open(self) -> int:
        """Debut (ms) de la chandelle actuellement ouverte, a la resolution courante."""
        now = int(time.time() * 1000)
        res_ms = max(1, int(self.res_s * 1000))
        return now // res_ms * res_ms

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    # -- interne -----------------------------------------------------------
    def _enqueue_initial(self) -> None:
        # Pre-chargement MINIMAL : UNIQUEMENT le prefixe de la chandelle OUVERTE
        # (de son ouverture -> maintenant) pour chaque flux. Le reste de
        # l'historique (passe profond) est rempli par le collecteur en arriere-
        # plan ; le GUI ne fait que LIRE trades.db. Flux AFFICHE en tete.
        co = self._candle_open()
        keys = [self.active_key] + [k for k in self._feeds if k != self.active_key]
        for key in keys:
            for s in self.symbols:
                self._enqueue(key, s, co)

    def _enqueue(self, key: str, symbol: str, start_ms: int) -> None:
        ck = (key, symbol)
        with self._lock:
            loaded = self._loaded.get(ck)
            if loaded is not None and start_ms >= loaded - 1000:
                return
            pend = self._pending.get(ck)
            if pend is not None and pend <= start_ms:
                return
            self._pending[ck] = start_ms
            self._queue.append((key, symbol, start_ms))
        self._wake.set()

    def _run(self) -> None:
        while not self._stop:
            self._wake.wait(FLUSH_INTERVAL)
            self._wake.clear()
            if self._stop:
                break
            # IMPORTANT : on traite UN SEUL element de backfill par tour, en
            # intercalant l'archivage live a chaque fois. Sinon, un backfill
            # initial long (dizaines de milliers de trades) monopolise le thread
            # et AFFAME _flush_live -> les trades live ne sont ni archives ni
            # suivis (_live_iv vide) pendant ~15s, d'ou le "trou" a l'ouverture.
            self._flush_live()
            self._process_one()
            if self._queue:           # reste du backfill -> reboucler vite
                self._wake.set()      #   (mais en repassant par _flush_live)

    def _note_live(self, fk: tuple[str, str], t0: int, t1: int) -> None:
        """Etend la couverture live du flux. Batch contigu au dernier intervalle
        (silence <= LIVE_GAP_TOL_MS) -> on l'allonge ; sinon -> nouvel intervalle
        (une coupure live s'est produite, le creux reste comblable par REST)."""
        iv = self._live_iv.setdefault(fk, [])
        if iv and t0 <= iv[-1][1] + LIVE_GAP_TOL_MS:
            iv[-1][1] = max(iv[-1][1], t1)
        else:
            iv.append([t0, t1])

    def _flush_live(self) -> None:
        for key, (tag, _) in self._feeds.items():
            for s in self.symbols:
                fk = (key, s)
                last = self._last_flush.get(fk, int(time.time() * 1000) - INITIAL_WINDOW_MS)
                new = self.hub.trades_since(key, s, last + 1)
                if new:
                    self.archive.insert(tag, new)
                    self._last_flush[fk] = new[-1].ts
                    self._note_live(fk, new[0].ts, new[-1].ts)

    def _process_one(self) -> None:
        """Traite UN element de la file de backfill (puis rend la main pour que
        _flush_live tourne entre deux fetchs)."""
        with self._lock:
            if not self._queue:
                return
            key, symbol, start_ms = self._queue.popleft()
        tag, fetch = self._feeds[key]
        try:
            trades = fetch(symbol, start_ms)
            # Anti double-comptage REST/live. CRUCIAL quand live et REST n'ont PAS le
            # meme espace d'ids -> Binance FUTURES : live @trade `t` (individuel) vs REST
            # aggTrades `a` (agrege) -> la PK (market,symbol,id) ne peut PAS dedupliquer.
            # (SPOT/Bitget/OKX/... partagent l'espace d'id -> la PK protege deja ; cette
            # regle y est juste sans effet, pas de perte.)
            # Le REST ne remplit que STRICTEMENT SOUS l'intervalle live LE PLUS RECENT
            # (`live_floor`) : cette zone (recente, ~jusqu'au present) appartient au live.
            # Sinon, un aggTrade REST arrive pendant l'aller-retour reseau (ou avant le 1er
            # flush live) tombe dans une zone que le live va couvrir -> double-comptage a la
            # couture. Les intervalles live ANTERIEURS sont exclus par _covered ; les creux
            # ENTRE eux (reconnexions) restent < live_floor -> comblables. Une reconnexion
            # future fait remonter live_floor -> le creux laisse devient comblable a son tour.
            iv = self._live_iv.get((key, symbol))
            now = int(time.time() * 1000)
            live_floor = iv[-1][0] if iv else now - LIVE_GAP_TOL_MS
            trades = [t for t in trades
                      if t.ts < live_floor and not (iv and _covered(t.ts, iv))]
            self.archive.insert(tag, trades)
            ck = (key, symbol)
            with self._lock:
                prev = self._loaded.get(ck)
                self._loaded[ck] = min(start_ms, prev) if prev else start_ms
                self._pending.pop(ck, None)
            log.info("backfill %s %s: %d trades (jusqu'a %d)",
                     key, symbol, len(trades), start_ms)
        except Exception as exc:  # noqa: BLE001 - reseau/API -> on reessaiera
            with self._lock:
                self._pending.pop((key, symbol), None)
            log.warning("backfill echec %s %s: %s", key, symbol, exc)
