"""Archive des trades sur disque (SQLite) pour l'orderflow historique.

But : conserver l'historique des trades (backfill REST + flux live) au-dela de ce
que garde le buffer memoire, sans saturer la RAM. Lecture par plage temporelle,
indexee -> on ne charge que la fenetre visible (pas tout l'historique).
Thread-safe (ecriture depuis le thread de backfill, lecture depuis Qt).
"""
from __future__ import annotations

import sqlite3
import threading

from .models import Trade

# Tick de PRIX de base du rollup, par symbole (= granularite a laquelle le footprint
# agrege est stocke). Doit etre <= au tick d'affichage en zoom arriere (qui est
# toujours plus grossier) -> aucune perte visible. Le zoom serre (<30 min) lit les
# trades BRUTS, pas le rollup, donc garde la pleine precision.
_BASE_TICK = {"BTCUSDT": 0.1, "ETHUSDT": 0.01}
ROLLUP_BUCKET_MS = 60_000   # resolution TEMPS de base du rollup (60 s)


def _base_tick(symbol: str) -> float:
    return _BASE_TICK.get(symbol, 0.01)


class TradeArchive:
    def __init__(self, path: str = "trades.db") -> None:
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()      # protege la connexion d'ECRITURE
        self._closed = False
        # LECTURES = connexion read-only PAR THREAD (WAL autorise plusieurs lecteurs
        # concurrents). Indispensable : aggregate_footprint scanne des millions de
        # lignes (~4 s en zoom arriere). Avec une connexion+lock UNIQUES, ce scan
        # gelait l'archivage live et le collecteur. Ici les lectures lourdes ne
        # touchent plus le lock d'ecriture ni la connexion d'ecriture.
        self._readers = threading.local()
        self._read_conns: list[sqlite3.Connection] = []
        self._read_conns_lock = threading.Lock()
        # Acces CONCURRENT (GUI temps reel + futur processus collecteur 24/7 sur la
        # MEME base) : mode WAL -> les lecteurs ne bloquent pas, un seul ecrivain a
        # la fois mais `busy_timeout` fait ATTENDRE au lieu d'echouer sur verrou.
        # La dedup logique est assuree par le PRIMARY KEY + INSERT OR IGNORE.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS trades("
            "market TEXT, symbol TEXT, ts INTEGER, price REAL, size REAL, "
            "side TEXT, trade_id TEXT, "
            "PRIMARY KEY(market, symbol, trade_id))"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ts ON trades(market, symbol, ts)"
        )
        # --- ROLLUP pre-agrege du footprint (base 60 s x tick d'instrument) ---
        # Maintenu INCREMENTALEMENT (rollup_step) par un curseur sur le rowid de
        # `trades` : tout trade reellement insere (live, gap-fill, backfill, meme
        # hors ordre) est agrege une seule fois. La lecture zoom arriere
        # (aggregate_footprint_rollup) re-bucketise temps+prix -> cout borne par
        # l'affichage, plus par le nombre de trades. side : 1=buy, 0=sell.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS footprint_rollup("
            "market TEXT, symbol TEXT, res INTEGER, bucket INTEGER, prow INTEGER, "
            "side INTEGER, vol REAL, "
            "PRIMARY KEY(market, symbol, res, bucket, prow, side)) WITHOUT ROWID"
        )
        # px_ask / px_bid = dernier prix d'un trade ACHETEUR / VENDEUR du bucket
        # (un acheteur agresseur tape l'ASK, un vendeur le BID) -> reconstruit les
        # lignes best bid/ask la ou le carnet n'a pas ete persiste (histo profond).
        # ts_ask / ts_bid servent au departage "dernier" lors du re-bucketing.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS footprint_ohlc("
            "market TEXT, symbol TEXT, res INTEGER, bucket INTEGER, "
            "ts_open INTEGER, px_open REAL, ts_close INTEGER, px_close REAL, "
            "ts_ask INTEGER, px_ask REAL, ts_bid INTEGER, px_bid REAL, "
            "PRIMARY KEY(market, symbol, res, bucket)) WITHOUT ROWID"
        )
        # MIGRATION : les colonnes bid/ask ont ete ajoutees apres coup -> ALTER pour
        # les bases existantes (les buckets deja agreges restent NULL, ceux a venir
        # se remplissent ; aucune migration de donnees a faire).
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(footprint_ohlc)")}
        for col, decl in (("ts_ask", "INTEGER"), ("px_ask", "REAL"),
                          ("ts_bid", "INTEGER"), ("px_bid", "REAL")):
            if col not in have:
                self._conn.execute(
                    f"ALTER TABLE footprint_ohlc ADD COLUMN {col} {decl}")
        # curseur de progression du rollup (rowid de trades deja agrege)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS rollup_state(k TEXT PRIMARY KEY, v INTEGER)"
        )
        self._conn.commit()

    def base_tick(self, symbol: str) -> float:
        """Tick de prix du rollup pour ce symbole (= plancher du tick d'affichage
        en zoom arriere)."""
        return _base_tick(symbol)

    def _reader(self) -> sqlite3.Connection:
        """Connexion SQLite read-only propre au thread appelant (creee a la
        demande). Les connexions sont enregistrees pour etre fermees par close()."""
        conn = getattr(self._readers, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA query_only=1")
            conn.execute("PRAGMA busy_timeout=5000")
            self._readers.conn = conn
            with self._read_conns_lock:
                self._read_conns.append(conn)
        return conn

    def insert(self, market: str, trades: list[Trade]) -> None:
        if not trades:
            return
        rows = [(market, t.symbol, t.ts, t.price, t.size, t.side, t.trade_id)
                for t in trades if t.trade_id]
        with self._lock:
            if self._closed:
                return
            self._conn.executemany(
                "INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?)", rows)
            self._conn.commit()

    def query(self, market: str, symbol: str, t0_ms: int, t1_ms: int) -> list[Trade]:
        if self._closed:
            return []
        cur = self._reader().execute(
            "SELECT ts, price, size, side, trade_id FROM trades "
            "WHERE market=? AND symbol=? AND ts>=? AND ts<=? ORDER BY ts",
            (market, symbol, t0_ms, t1_ms))
        rows = cur.fetchall()
        # exchange = le tag de marche reel (ex. "okx-swap") : l'archive ne stocke pas le
        # libelle d'exchange, mais surtout PAS "bitget" en dur (label trompeur pour les
        # flux non-Bitget ; seuls ts/price/size/side/id sont consommes en aval).
        return [Trade(market, symbol, p, s, side, ts, tid)
                for (ts, p, s, side, tid) in rows]

    def bbo_series(self, market: str, symbol: str, t0_ms: int, t1_ms: int,
                   res_ms: int):
        """Serie best bid/ask FINE depuis les trades BRUTS, regroupee par bucket de
        `res_ms` : par bucket, dernier prix ACHETEUR (= ask) et dernier VENDEUR
        (= bid), via l'astuce MAX(ts) (le prix nu vient de la ligne au max). Cout
        BORNE par le nb de buckets (l'appelant choisit res_ms = plage/N), fait en
        SQL (C). Sert a tracer des lignes bid/ask FINES (sub-seconde possible) la ou
        le carnet manque, en zoom modere (l'appelant plafonne la plage scannee).
        Renvoie (ask, bid) = listes [(bucket, max_ts, px)]."""
        if self._closed:
            return [], []
        w = "market=? AND symbol=? AND ts>=? AND ts<=?"
        p = (market, symbol, t0_ms, t1_ms)
        conn = self._reader()
        ask = conn.execute(
            f"SELECT ts/?, MAX(ts), price FROM trades WHERE {w} AND side='buy' GROUP BY ts/?",
            (res_ms, *p, res_ms)).fetchall()
        bid = conn.execute(
            f"SELECT ts/?, MAX(ts), price FROM trades WHERE {w} AND side='sell' GROUP BY ts/?",
            (res_ms, *p, res_ms)).fetchall()
        return ask, bid

    def aggregate_footprint(self, market: str, symbol: str, t0_ms: int, t1_ms: int,
                            res_ms: int, tick: float):
        """Agrege les trades EN SQL pour le footprint : volume par (bougie, niveau
        de prix, sens) + open/close par bougie. Le resultat est BORNE (nb bougies ×
        niveaux), independamment du nombre de trades -> rend le zoom arriere viable
        meme sur des millions de trades / plusieurs exchanges. SQLite fait le travail
        en C (pas de boucle Python, pas d'objets Trade crees). Renvoie (vol, opn, cls)
        en lignes brutes ; la GUI (candles.py) assemble les Candle."""
        w = "market=? AND symbol=? AND ts>=? AND ts<=?"
        p = (market, symbol, t0_ms, t1_ms)
        if self._closed:
            return [], [], [], [], []
        conn = self._reader()           # connexion read-only du thread (hors lock ecriture)
        vol = conn.execute(
            f"SELECT ts/?, CAST(ROUND(price/?) AS INT), side, SUM(size) "
            f"FROM trades WHERE {w} GROUP BY ts/?, CAST(ROUND(price/?) AS INT), side",
            (res_ms, tick, *p, res_ms, tick)).fetchall()
        # open = prix du trade au MIN(ts) de la bougie (colonne nue + MIN(ts)) ;
        # close = prix au MAX(ts). Astuce SQLite : avec un seul min()/max(), les
        # colonnes nues proviennent de la ligne qui porte ce min/max.
        opn = conn.execute(
            f"SELECT ts/?, MIN(ts), price FROM trades WHERE {w} GROUP BY ts/?",
            (res_ms, *p, res_ms)).fetchall()
        cls = conn.execute(
            f"SELECT ts/?, MAX(ts), price FROM trades WHERE {w} GROUP BY ts/?",
            (res_ms, *p, res_ms)).fetchall()
        # ask/bid = prix du DERNIER trade acheteur / vendeur de la bougie (meme
        # astuce MAX(ts) -> prix de cette ligne).
        ask = conn.execute(
            f"SELECT ts/?, MAX(ts), price FROM trades WHERE {w} AND side='buy' GROUP BY ts/?",
            (res_ms, *p, res_ms)).fetchall()
        bid = conn.execute(
            f"SELECT ts/?, MAX(ts), price FROM trades WHERE {w} AND side='sell' GROUP BY ts/?",
            (res_ms, *p, res_ms)).fetchall()
        return vol, opn, cls, ask, bid


    def earliest_id(self, market: str, symbol: str) -> str | None:
        """trade_id du trade le plus ANCIEN en base (curseur de depart du
        collecteur pour remonter le passe). None si rien d'archive encore."""
        if self._closed:
            return None
        cur = self._reader().execute(
            "SELECT trade_id FROM trades WHERE market=? AND symbol=? "
            "ORDER BY ts ASC LIMIT 1",
            (market, symbol))
        row = cur.fetchone()
        return row[0] if row else None

    def id_at_or_after(self, market: str, symbol: str, ts: int) -> str | None:
        """trade_id du PREMIER trade archive a ts >= `ts` (= bord superieur d'un trou,
        le 1er trade APRES la coupure). Sert a AMORCER fetch_before pile au-dessus du
        trou pour y descendre directement, sans repaginer la zone deja pleine. Sur
        Bitget l'id WS == l'id REST (cf. docs/bitget.md) -> amorcage sur id archive sur.
        None si rien a ts >= `ts`."""
        if self._closed:
            return None
        cur = self._reader().execute(
            "SELECT trade_id FROM trades WHERE market=? AND symbol=? AND ts >= ? "
            "ORDER BY ts ASC LIMIT 1",
            (market, symbol, ts))
        row = cur.fetchone()
        return row[0] if row else None

    def earliest(self, market: str, symbol: str) -> int | None:
        if self._closed:
            return None
        cur = self._reader().execute(
            "SELECT MIN(ts) FROM trades WHERE market=? AND symbol=?",
            (market, symbol))
        row = cur.fetchone()
        return row[0] if row else None

    def find_holes(self, market: str, symbol: str, threshold_ms: int,
                   since_ms: int | None = None) -> list[tuple[int, int]]:
        """Detecte les TROUS de l'archive : intervalles (a, b) ou deux trades
        consecutifs sont separes de plus de `threshold_ms` (= l'app etait eteinte ;
        sur BTC/ETH les trades arrivent en continu, un grand ecart = une coupure).
        `a` = dernier trade avant le trou, `b` = premier apres. Tries du plus
        ANCIEN au plus recent. Sert au collecteur pour combler TOUS les trous, pas
        seulement le dernier.

        `since_ms` : si fourni, ne scanne que les trades de ts >= since_ms. La
        fonction (LAG sur TOUTES les lignes du flux) coute O(lignes) ; sur une
        archive volumineuse (ex. import CSV de millions de trades) un scan complet
        prend plusieurs secondes et FIGE le collecteur a chaque passe. Borner a une
        fenetre RECENTE rend le scan ~instantane (index (market,symbol,ts)). Un trou
        qui DEBUTE avant la fenetre n'est alors pas vu ici, mais le trou de TETE
        (fin session precedente) est, lui, geré a part par le collecteur."""
        if self._closed:
            return []
        sql = ("SELECT prev_ts, ts FROM ("
               "  SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev_ts"
               "  FROM trades WHERE market=? AND symbol=?{since}"
               ") WHERE prev_ts IS NOT NULL AND ts - prev_ts > ? ORDER BY ts")
        params: list = [market, symbol]
        if since_ms is not None:
            sql = sql.format(since=" AND ts >= ?")
            params.append(int(since_ms))
        else:
            sql = sql.format(since="")
        params.append(threshold_ms)
        cur = self._reader().execute(sql, params)
        return [(int(a), int(b)) for (a, b) in cur.fetchall()]

    def newest(self, market: str, symbol: str) -> int | None:
        """ts (ms) du trade le plus RECENT en base. Sert au collecteur pour
        reperer le TROU entre la fin de la derniere session et maintenant."""
        if self._closed:
            return None
        cur = self._reader().execute(
            "SELECT MAX(ts) FROM trades WHERE market=? AND symbol=?",
            (market, symbol))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None

    # -- ROLLUP : maintenance incrementale ---------------------------------
    def rollup_step(self, limit: int = 20000) -> int:
        """Agrege le PROCHAIN paquet de trades (par rowid croissant) dans le rollup.
        Renvoie le nombre de trades traites (0 = a jour). Appele en boucle par le
        RollupMaintainer. Lit+ecrit sous le lock d'ecriture (paquet borne -> court).
        Le curseur rowid garantit qu'aucun trade n'est compte deux fois, meme
        insere hors ordre (gap-fill)."""
        with self._lock:
            if self._closed:
                return 0
            r = self._conn.execute(
                "SELECT v FROM rollup_state WHERE k='last_rowid'").fetchone()
            last = r[0] if r else 0
            rows = self._conn.execute(
                "SELECT rowid, market, symbol, ts, price, size, side FROM trades "
                "WHERE rowid > ? ORDER BY rowid LIMIT ?", (last, limit)).fetchall()
            if not rows:
                return 0
            vol_cells: dict[tuple, float] = {}
            ohlc: dict[tuple, list] = {}
            max_rowid = last
            for rid, market, symbol, ts, price, size, side in rows:
                if rid > max_rowid:
                    max_rowid = rid
                bt = _base_tick(symbol)
                bucket = ts // ROLLUP_BUCKET_MS
                prow = int(round(price / bt))
                side_i = 1 if side == "buy" else 0
                k = (market, symbol, bucket, prow, side_i)
                vol_cells[k] = vol_cells.get(k, 0.0) + size
                ok = (market, symbol, bucket)
                o = ohlc.get(ok)
                # [ts_open, px_open, ts_close, px_close, ts_ask, px_ask, ts_bid, px_bid]
                # ask = dernier prix ACHETEUR (side==buy), bid = dernier VENDEUR.
                if o is None:
                    o = [ts, price, ts, price, None, None, None, None]
                    ohlc[ok] = o
                else:
                    if ts < o[0]:
                        o[0], o[1] = ts, price
                    if ts > o[2]:
                        o[2], o[3] = ts, price
                if side == "buy":
                    if o[4] is None or ts >= o[4]:
                        o[4], o[5] = ts, price
                else:
                    if o[6] is None or ts >= o[6]:
                        o[6], o[7] = ts, price
            self._conn.executemany(
                "INSERT INTO footprint_rollup(market,symbol,res,bucket,prow,side,vol) "
                "VALUES(?,?,60,?,?,?,?) "
                "ON CONFLICT(market,symbol,res,bucket,prow,side) "
                "DO UPDATE SET vol=vol+excluded.vol",
                [(m, s, b, pr, si, v) for (m, s, b, pr, si), v in vol_cells.items()])
            # bid/ask : on garde le prix du trade au ts le PLUS RECENT de son cote.
            # Gestion des NULL (un cote peut n'avoir jamais trade dans le bucket) :
            # si l'ancien est NULL on prend l'entrant ; si l'entrant est NULL on garde.
            self._conn.executemany(
                "INSERT INTO footprint_ohlc("
                "market,symbol,res,bucket,ts_open,px_open,ts_close,px_close,"
                "ts_ask,px_ask,ts_bid,px_bid) "
                "VALUES(?,?,60,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(market,symbol,res,bucket) DO UPDATE SET "
                "px_open=CASE WHEN excluded.ts_open<ts_open THEN excluded.px_open ELSE px_open END,"
                "ts_open=MIN(ts_open,excluded.ts_open),"
                "px_close=CASE WHEN excluded.ts_close>ts_close THEN excluded.px_close ELSE px_close END,"
                "ts_close=MAX(ts_close,excluded.ts_close),"
                "px_ask=CASE WHEN excluded.ts_ask IS NOT NULL AND (ts_ask IS NULL OR excluded.ts_ask>=ts_ask) THEN excluded.px_ask ELSE px_ask END,"
                "ts_ask=CASE WHEN excluded.ts_ask IS NOT NULL AND (ts_ask IS NULL OR excluded.ts_ask>=ts_ask) THEN excluded.ts_ask ELSE ts_ask END,"
                "px_bid=CASE WHEN excluded.ts_bid IS NOT NULL AND (ts_bid IS NULL OR excluded.ts_bid>=ts_bid) THEN excluded.px_bid ELSE px_bid END,"
                "ts_bid=CASE WHEN excluded.ts_bid IS NOT NULL AND (ts_bid IS NULL OR excluded.ts_bid>=ts_bid) THEN excluded.ts_bid ELSE ts_bid END",
                [(m, s, b, o[0], o[1], o[2], o[3], o[4], o[5], o[6], o[7])
                 for (m, s, b), o in ohlc.items()])
            self._conn.execute(
                "INSERT INTO rollup_state(k,v) VALUES('last_rowid',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (max_rowid,))
            self._conn.commit()
            return len(rows)

    def aggregate_footprint_rollup(self, market: str, symbol: str,
                                   t0_ms: int, t1_ms: int, res_s: float, tick: float):
        """Footprint agrege LU DEPUIS LE ROLLUP (au lieu de scanner les trades
        bruts). Re-bucketise le temps (60 s -> res_s) et le prix (base_tick ->
        tick d'affichage) -> cout proportionnel a la TAILLE DU ROLLUP dans la
        fenetre, pas au nombre de trades. Meme format de retour que
        aggregate_footprint. Repli automatique sur le brut si le rollup ne couvre
        pas encore la fenetre (ex. juste apres demarrage)."""
        if self._closed:
            return [], [], [], [], []
        factor = max(1, int(round(res_s / 60.0)))   # 60 s -> res_s
        base = _base_tick(symbol)
        b0 = t0_ms // ROLLUP_BUCKET_MS
        b1 = t1_ms // ROLLUP_BUCKET_MS
        conn = self._reader()
        # NB : SQLite ne supporte PAS le GROUP BY positionnel (GROUP BY 1 = constante
        # -> un seul groupe). On repete donc les expressions (le param `factor` est
        # rebinde pour le GROUP BY ; `drow` alias est, lui, accepte par SQLite).
        vol = conn.execute(
            "SELECT bucket/?, CAST(ROUND(prow*?/?) AS INT) AS drow, "
            "CASE WHEN side=1 THEN 'buy' ELSE 'sell' END, SUM(vol) "
            "FROM footprint_rollup "
            "WHERE market=? AND symbol=? AND res=60 AND bucket BETWEEN ? AND ? "
            "GROUP BY bucket/?, drow, side",
            (factor, base, tick, market, symbol, b0, b1, factor)).fetchall()
        if not vol:
            # rollup pas encore disponible pour cette fenetre -> repli sur le brut
            return self.aggregate_footprint(market, symbol, t0_ms, t1_ms,
                                            max(1, int(res_s * 1000)), tick)
        opn = conn.execute(
            "SELECT bucket/?, MIN(ts_open), px_open FROM footprint_ohlc "
            "WHERE market=? AND symbol=? AND res=60 AND bucket BETWEEN ? AND ? GROUP BY bucket/?",
            (factor, market, symbol, b0, b1, factor)).fetchall()
        cls = conn.execute(
            "SELECT bucket/?, MAX(ts_close), px_close FROM footprint_ohlc "
            "WHERE market=? AND symbol=? AND res=60 AND bucket BETWEEN ? AND ? GROUP BY bucket/?",
            (factor, market, symbol, b0, b1, factor)).fetchall()
        # ask/bid re-bucketise : prix du cote au ts le plus RECENT du sous-bucket
        # (NULL ignores -> un cote absent ne casse pas le report cote affichage).
        ask = conn.execute(
            "SELECT bucket/?, MAX(ts_ask), px_ask FROM footprint_ohlc "
            "WHERE market=? AND symbol=? AND res=60 AND bucket BETWEEN ? AND ? "
            "AND ts_ask IS NOT NULL GROUP BY bucket/?",
            (factor, market, symbol, b0, b1, factor)).fetchall()
        bid = conn.execute(
            "SELECT bucket/?, MAX(ts_bid), px_bid FROM footprint_ohlc "
            "WHERE market=? AND symbol=? AND res=60 AND bucket BETWEEN ? AND ? "
            "AND ts_bid IS NOT NULL GROUP BY bucket/?",
            (factor, market, symbol, b0, b1, factor)).fetchall()
        return vol, opn, cls, ask, bid

    @staticmethod
    def _mids_by_bucket(ask, bid, cls) -> dict:
        """mid par bucket = (px_ask+px_bid)/2 ; repli px_close, puis un seul cote."""
        a = {cb: px for cb, _, px in ask}
        b = {cb: px for cb, _, px in bid}
        c = {cb: px for cb, _, px in cls}
        out: dict = {}
        for cb in set(a) | set(b) | set(c):
            if cb in a and cb in b:
                out[cb] = (a[cb] + b[cb]) / 2.0
            else:
                out[cb] = c.get(cb, a.get(cb, b.get(cb)))
        return out

    def aggregate_footprint_rollup_hybrid(self, base_market: str,
                                          feed_markets, symbol: str,
                                          t0_ms: int, t1_ms: int,
                                          res_s: float, tick: float,
                                          base_vol: bool = True,
                                          basis_by_feed: dict | None = None):
        """Footprint HYBRIDE (zoom arriere) : somme des rollups de plusieurs marches,
        chacun RECALE sur la reference (Bitget) par un offset PAR BUCKET. Le corps OHLC
        et les lignes bid/ask = ceux de la REFERENCE (toujours, c'est l'axe) ;
        `base_vol`=False -> Bitget reste la reference mais son VOLUME n'est pas compte.

        RECALAGE (revue #6) : si `basis_by_feed` fournit l'offset TRADE-MATCHE figé du live
        ({feed_market: {cb: offset}}, offset = Bitget-feed a AJOUTER au feed) pour le bucket
        cb, on l'utilise -> MEME basis que le chemin live a la couture AGG_SPAN_S (plus de
        saut de rang). Sinon (historique profond, hors couverture du live) -> REPLI sur le
        basis des mids du rollup (px_ask/px_bid). cf. docs/hybride.md."""
        vol_b, opn, cls, ask, bid = self.aggregate_footprint_rollup(
            base_market, symbol, t0_ms, t1_ms, res_s, tick)
        if not feed_markets and base_vol:
            return vol_b, opn, cls, ask, bid
        mid_b = self._mids_by_bucket(ask, bid, cls)
        combined: dict = {}
        if base_vol:
            for cb, row, side, v in vol_b:
                combined[(cb, row, side)] = combined.get((cb, row, side), 0.0) + v
        for fm in feed_markets:
            v_e, _o, c_e, a_e, b_e = self.aggregate_footprint_rollup(
                fm, symbol, t0_ms, t1_ms, res_s, tick)
            if not v_e:
                continue
            mid_e = self._mids_by_bucket(a_e, b_e, c_e)
            live_basis = (basis_by_feed or {}).get(fm)
            for cb, row, side, v in v_e:
                off = live_basis.get(cb) if live_basis else None   # basis trade-matché du live
                if off is None:                                    # repli : basis des mids
                    mb, me = mid_b.get(cb), mid_e.get(cb)
                    off = (mb - me) if (mb is not None and me is not None) else None
                drow = int(round(off / tick)) if (off is not None and tick > 0) else 0
                k = (cb, row + drow, side)
                combined[k] = combined.get(k, 0.0) + v
        vol = [(cb, row, side, v) for (cb, row, side), v in combined.items()]
        return vol, opn, cls, ask, bid

    def purge(self, cutoff_ms: int) -> int:
        """Retention : supprime les trades bruts plus vieux que cutoff_ms ET DEJA
        agreges dans le rollup (rowid <= curseur). Le footprint historique survit
        dans le rollup ; les scatters sont de toute facon la derniere heure."""
        with self._lock:
            if self._closed:
                return 0
            r = self._conn.execute(
                "SELECT v FROM rollup_state WHERE k='last_rowid'").fetchone()
            last = r[0] if r else 0
            n = self._conn.execute(
                "DELETE FROM trades WHERE ts < ? AND rowid <= ?",
                (cutoff_ms, last)).rowcount
            self._conn.commit()
        return n

    def purge_rollup(self, cutoff_ms: int) -> int:
        """Borne le ROLLUP : supprime les buckets de footprint plus vieux que cutoff_ms.
        Le rollup conserve le footprint historique BIEN au-dela des trades bruts (la
        retention 7 j ne touche QUE `trades`), mais pas indefiniment -> sans cet horizon
        DUR, footprint_rollup/_ohlc croissent sans fin (revue #7). `bucket` est un INDEX
        (ts // ROLLUP_BUCKET_MS) -> on le compare au bucket du cutoff. Renvoie le nombre
        de cellules de volume supprimees."""
        with self._lock:
            if self._closed:
                return 0
            cb = cutoff_ms // ROLLUP_BUCKET_MS
            n = self._conn.execute(
                "DELETE FROM footprint_rollup WHERE bucket < ?", (cb,)).rowcount
            self._conn.execute(
                "DELETE FROM footprint_ohlc WHERE bucket < ?", (cb,))
            self._conn.commit()
        return n

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()
        # fermer les connexions de lecture ouvertes par les threads de fond
        with self._read_conns_lock:
            for c in self._read_conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._read_conns.clear()
