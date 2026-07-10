"""Journal des opportunites d'arbitrage detectees (SQLite, base SEPAREE data/arb.db).

Une ligne = une opportunite signalee (>= seuil) a un instant : qui vendre / qui acheter,
l'ecart brut et le profit net (bps + $). Volume faible (ecriture throttlee cote GUI) ->
une seule connexion + lock suffit. WAL + busy_timeout comme les autres bases. Purge de
retention identique (par ts).
"""
from __future__ import annotations

import sqlite3
import threading


class ArbArchive:
    def __init__(self, path: str = "arb.db") -> None:
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._closed = False
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS arbitrage ("
            "ts INTEGER, symbol TEXT, market TEXT, sell_ex TEXT, buy_ex TEXT, "
            "edge REAL, net_bps REAL, net_usd REAL)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_arb_ts ON arbitrage(ts)")
        self._conn.commit()

    def insert(self, ts: int, symbol: str, market: str, sell_ex: str,
               buy_ex: str, edge: float, net_bps: float, net_usd: float) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.execute(
                "INSERT INTO arbitrage VALUES (?,?,?,?,?,?,?,?)",
                (ts, symbol, market, sell_ex, buy_ex, edge, net_bps, net_usd))
            self._conn.commit()

    def query(self, since_ms: int, symbol: str | None = None) -> list[tuple]:
        with self._lock:
            if self._closed:
                return []
            if symbol is None:
                cur = self._conn.execute(
                    "SELECT ts,symbol,market,sell_ex,buy_ex,edge,net_bps,net_usd "
                    "FROM arbitrage WHERE ts >= ? ORDER BY ts", (since_ms,))
            else:
                cur = self._conn.execute(
                    "SELECT ts,symbol,market,sell_ex,buy_ex,edge,net_bps,net_usd "
                    "FROM arbitrage WHERE ts >= ? AND symbol = ? ORDER BY ts",
                    (since_ms, symbol))
            return cur.fetchall()

    def purge(self, cutoff_ms: int) -> int:
        with self._lock:
            if self._closed:
                return 0
            n = self._conn.execute(
                "DELETE FROM arbitrage WHERE ts < ?", (cutoff_ms,)).rowcount
            self._conn.commit()
        return n

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()
