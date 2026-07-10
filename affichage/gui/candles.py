"""Reconstruction de bougies + footprint a partir des TRADES.

Choix d'architecture : on ne se connecte PAS au canal candle de l'exchange. Le
footprint exige de toute facon le detail des trades par prix (volume acheteur /
vendeur par niveau) que le canal candle ne fournit pas. En agregeant les memes
trades, la bougie et son footprint sont parfaitement coherents, et toutes les
resolutions (1m, 5m, ...) se deduisent par simple re-bucketing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from backend.models import Trade


@dataclass(slots=True)
class Candle:
    """Une bougie + son footprint (volume acheteur/vendeur par niveau de prix)."""
    t0: float                                   # debut (s, horloge exchange)
    t1: float                                   # fin (s)
    o: float
    h: float
    l: float
    c: float
    tick: float                                 # pas de regroupement des prix
    buy: dict[int, float] = field(default_factory=dict)   # row -> volume acheteur
    sell: dict[int, float] = field(default_factory=dict)  # row -> volume vendeur
    # best bid/ask RECONSTRUITS : dernier prix vendeur / acheteur de la bougie
    # (proxy du carnet la ou books.db n'a rien). None si ce cote n'a pas trade.
    bid: float | None = None
    ask: float | None = None

    @property
    def buy_total(self) -> float:
        return sum(self.buy.values())

    @property
    def sell_total(self) -> float:
        return sum(self.sell.values())

    @property
    def delta(self) -> float:
        return self.buy_total - self.sell_total


def build_candles_agg(vol, opn, cls, ask, bid, res_s: float, tick: float) -> list[Candle]:
    """Assemble les bougies a partir de l'agregat SQL (archive.aggregate_footprint)
    au lieu d'iterer les trades bruts. Cout = O(bougies × niveaux), borne.

    vol : (cb, row, side, sum_size)  | opn/cls/ask/bid : (cb, _, price)
    cb = index de bougie (ts_ms // res_ms). High/Low deduits des niveaux presents
    (resolution du tick -> suffisant en zoom arriere). ask/bid = best ask/bid
    reconstruits (dernier prix acheteur / vendeur)."""
    if res_s <= 0 or tick <= 0:
        return []
    opens = {cb: px for cb, _, px in opn}
    closes = {cb: px for cb, _, px in cls}
    asks = {cb: px for cb, _, px in ask}
    bids = {cb: px for cb, _, px in bid}
    by: dict[int, Candle] = {}
    for cb, row, side, sm in vol:
        c = by.get(cb)
        if c is None:
            t0 = cb * res_s
            o = opens.get(cb, row * tick)
            cl = closes.get(cb, row * tick)
            c = Candle(t0=t0, t1=t0 + res_s, o=o, h=o, l=o, c=cl, tick=tick,
                       bid=bids.get(cb), ask=asks.get(cb))
            by[cb] = c
        if side == "buy":
            c.buy[row] = c.buy.get(row, 0.0) + sm
        else:
            c.sell[row] = c.sell.get(row, 0.0) + sm
    # High/Low = bornes des niveaux remplis (+ open/close pour ne rien rater)
    for c in by.values():
        rows = list(c.buy) + list(c.sell)
        if rows:
            c.h = max(c.h, c.c, max(rows) * tick)
            c.l = min(c.l, c.c, min(rows) * tick)
    return [by[k] for k in sorted(by)]


def merge_hybrid_candles(entries: list) -> list[Candle]:
    """Fusionne des bougies de PLUSIEURS exchanges, deja recalees, en une seule
    serie hybride (footprint = TOTAL FUSIONNE). `entries` = [(candles, drow, dpx, with_vol)]
    avec la REFERENCE (Bitget) EN PREMIER (drow=0, dpx=0).

    - `drow` = decalage de RANGS de prix (round(spread/tick)) applique a l'histogramme
      bid/ask de cet exchange (recalage sur l'axe Bitget).
    - `dpx`  = decalage de PRIX (spread) applique au corps OHLC d'un bucket cree par
      un exchange non-reference (la ou Bitget n'a pas trade).
    - `with_vol` : ajoute le VOLUME de cet exchange (False = Bitget decoche -> il reste
      la REFERENCE de prix/corps/bid-ask mais son footprint n'est pas affiche).
    Le corps OHLC et les lignes bid/ask viennent de la REFERENCE (1er entry) ; les
    autres n'apportent que du VOLUME (recale)."""
    out: dict[float, Candle] = {}
    for idx, (cands, drow, dpx, with_vol) in enumerate(entries):
        is_ref = (idx == 0)
        for c in cands:
            m = out.get(c.t0)
            if m is None:
                m = Candle(t0=c.t0, t1=c.t1, o=c.o + dpx, h=c.h + dpx, l=c.l + dpx,
                           c=c.c + dpx, tick=c.tick,
                           bid=(c.bid if is_ref else None),
                           ask=(c.ask if is_ref else None))
                out[c.t0] = m
            if not with_vol:
                continue
            for row, v in c.buy.items():
                r = row + drow
                m.buy[r] = m.buy.get(r, 0.0) + v
            for row, v in c.sell.items():
                r = row + drow
                m.sell[r] = m.sell.get(r, 0.0) + v
    # High/Low = bornes des niveaux remplis (+ open/close), comme build_candles_agg
    for m in out.values():
        rows = list(m.buy) + list(m.sell)
        if rows:
            m.h = max(m.h, m.c, max(rows) * m.tick)
            m.l = min(m.l, m.c, min(rows) * m.tick)
    return [out[k] for k in sorted(out)]


def build_candles(trades: list[Trade], res_s: float, tick: float) -> list[Candle]:
    """Agrege une liste de trades (triee par ts croissant) en bougies de `res_s`
    secondes, chaque prix regroupe par `tick`. Open = 1er trade, Close = dernier."""
    if not trades or res_s <= 0 or tick <= 0:
        return []
    by_bucket: dict[float, Candle] = {}
    for t in trades:
        ts = t.ts / 1000.0
        b0 = math.floor(ts / res_s) * res_s
        c = by_bucket.get(b0)
        if c is None:
            c = Candle(t0=b0, t1=b0 + res_s, o=t.price, h=t.price, l=t.price,
                       c=t.price, tick=tick)
            by_bucket[b0] = c
        else:
            if t.price > c.h:
                c.h = t.price
            if t.price < c.l:
                c.l = t.price
            c.c = t.price
        row = int(round(t.price / tick))
        # trades tries par ts croissant -> la derniere affectation par cote = le
        # dernier prix acheteur (ask) / vendeur (bid) de la bougie.
        if t.side == "buy":
            c.buy[row] = c.buy.get(row, 0.0) + t.size
            c.ask = t.price
        else:
            c.sell[row] = c.sell.get(row, 0.0) + t.size
            c.bid = t.price
    return [by_bucket[k] for k in sorted(by_bucket)]
