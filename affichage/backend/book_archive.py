"""Archive des snapshots de carnet sur disque (SQLite) pour la heatmap historique.

Le carnet n'a AUCUN historique REST (contrairement aux trades) : la seule facon
de constituer un historique de liquidite est de PERSISTER les snapshots live au
fil de l'eau. L'historique du carnet ne grandit donc que VERS L'AVANT, pendant
les sessions ou l'app tourne.

Base SEPAREE de `trades.db` (`books.db`) : volume et schema tres differents,
cycles de vie independants (purgeable sans toucher aux trades). Meme robustesse
concurrente (WAL + busy_timeout) car le GUI ecrit pendant que d'autres lecteurs
(rendu de la heatmap historique) lisent.

OPTION DE STOCKAGE = 1 (depth BRUT) : une ligne = une colonne de carnet, les
niveaux (prix/tailles) serialises en BLOB numpy compact. Le schema est isole
derriere `insert_snapshot` / `query` -> on pourra basculer vers une heatmap
bucketisee (option 2) ou hybride (3) sans toucher au reste du code.
"""
from __future__ import annotations

import sqlite3
import threading

import numpy as np

# dtypes des niveaux serialises (memes que gui/history.py : prix f64, tailles f32)
_PRICE_DT = np.float64
_SIZE_DT = np.float32


class BookSnapshot:
    __slots__ = ("ts", "bid", "ask", "prices", "sizes")

    def __init__(self, ts: int, bid: float, ask: float,
                 prices: np.ndarray, sizes: np.ndarray) -> None:
        self.ts = ts            # ms UTC (horloge de l'exchange, comme les trades)
        self.bid = bid          # best bid au moment du snapshot (pour le spread)
        self.ask = ask          # best ask
        self.prices = prices    # niveaux : prix (bids + asks)
        self.sizes = sizes      # niveaux : tailles


class BookArchive:
    def __init__(self, path: str = "books.db") -> None:
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()      # protege la connexion d'ECRITURE
        self._closed = False
        # Lectures (heatmap historique) = connexion read-only par thread, hors du
        # lock d'ecriture (cf. TradeArchive). Le BookRecorder ecrit a 1 Hz pendant
        # que le BookReader lit -> ils ne se bloquent plus mutuellement.
        self._readers = threading.local()
        self._read_conns: list[sqlite3.Connection] = []
        self._read_conns_lock = threading.Lock()
        # Acces concurrent (ecriture GUI + lectures heatmap historique) -> WAL.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # best_bid/best_ask stockes par snapshot : ce sont les MID dont la
        # normalisation hybride (spread bitget vs autres) aura besoin.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS snapshots("
            "market TEXT, symbol TEXT, ts INTEGER, bid REAL, ask REAL, "
            "prices BLOB, sizes BLOB, "
            "PRIMARY KEY(market, symbol, ts))"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(market, symbol, ts)"
        )
        self._conn.commit()

    def _reader(self) -> sqlite3.Connection:
        """Connexion read-only propre au thread appelant (creee a la demande)."""
        conn = getattr(self._readers, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA query_only=1")
            conn.execute("PRAGMA busy_timeout=5000")
            self._readers.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    def insert_snapshot(self, market: str, symbol: str, ts: int,
                        bid: float, ask: float,
                        prices: np.ndarray, sizes: np.ndarray) -> None:
        """Persiste une colonne de carnet. INSERT OR IGNORE : un meme (market,
        symbol, ts) n'est ecrit qu'une fois (dedup si la cadence se chevauche)."""
        blob_p = np.ascontiguousarray(prices, dtype=_PRICE_DT).tobytes()
        blob_s = np.ascontiguousarray(sizes, dtype=_SIZE_DT).tobytes()
        with self._lock:
            if self._closed:
                return
            self._conn.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?,?)",
                (market, symbol, int(ts), float(bid), float(ask), blob_p, blob_s))
            self._conn.commit()

    def query(self, market: str, symbol: str,
              t0_ms: int, t1_ms: int) -> list[BookSnapshot]:
        if self._closed:
            return []
        cur = self._reader().execute(
            "SELECT ts, bid, ask, prices, sizes FROM snapshots "
            "WHERE market=? AND symbol=? AND ts>=? AND ts<=? ORDER BY ts",
            (market, symbol, t0_ms, t1_ms))
        rows = cur.fetchall()
        return [BookSnapshot(ts, bid, ask,
                             np.frombuffer(bp, dtype=_PRICE_DT),
                             np.frombuffer(bs, dtype=_SIZE_DT))
                for (ts, bid, ask, bp, bs) in rows]

    def query_sampled(self, market: str, symbol: str,
                      t0_ms: int, t1_ms: int, max_cols: int) -> list[BookSnapshot]:
        """Lecture BORNEE : au plus ~max_cols snapshots REPARTIS sur [t0, t1],
        quel que soit le nombre reel de lignes dans la plage. On decoupe la
        fenetre en max_cols tranches de temps egales et on prend UN snapshot par
        tranche (le plus ancien). Le cout depend de la PLAGE demandee, pas de la
        taille de la base -> pas de gel meme sur une enorme books.db.

        (MIN(ts) + colonnes nues : SQLite renvoie alors les valeurs de la LIGNE
        qui porte ce MIN(ts) dans chaque groupe -> un vrai snapshot, pas un melange.)
        """
        span = max(t1_ms - t0_ms, 1)
        bucket = max(1, span // max(1, max_cols))
        if self._closed:
            return []
        cur = self._reader().execute(
            "SELECT MIN(ts) AS ts, bid, ask, prices, sizes FROM snapshots "
            "WHERE market=? AND symbol=? AND ts>=? AND ts<=? "
            "GROUP BY (ts - ?) / ? ORDER BY ts",
            (market, symbol, t0_ms, t1_ms, t0_ms, bucket))
        rows = cur.fetchall()
        return [BookSnapshot(ts, bid, ask,
                             np.frombuffer(bp, dtype=_PRICE_DT),
                             np.frombuffer(bs, dtype=_SIZE_DT))
                for (ts, bid, ask, bp, bs) in rows]

    def earliest(self, market: str, symbol: str) -> int | None:
        if self._closed:
            return None
        cur = self._reader().execute(
            "SELECT MIN(ts) FROM snapshots WHERE market=? AND symbol=?",
            (market, symbol))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None

    def count(self, market: str, symbol: str) -> int:
        if self._closed:
            return 0
        cur = self._reader().execute(
            "SELECT COUNT(*) FROM snapshots WHERE market=? AND symbol=?",
            (market, symbol))
        return int(cur.fetchone()[0])

    def purge(self, cutoff_ms: int) -> int:
        """Retention : supprime les snapshots de carnet plus vieux que cutoff_ms."""
        with self._lock:
            if self._closed:
                return 0
            n = self._conn.execute(
                "DELETE FROM snapshots WHERE ts < ?", (cutoff_ms,)).rowcount
            self._conn.commit()
        return n

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()
        with self._read_conns_lock:
            for c in self._read_conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._read_conns.clear()
