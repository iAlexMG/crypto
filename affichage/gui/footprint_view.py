"""Vue FOOTPRINT (pane gauche) facon Quantower : chandelier + histogramme bid/ask.

Reconstruit les bougies depuis les TRADES (voir gui/candles.py), alignees sur les
vraies bornes 1m/5m/... (epoch -> debut a X:00). Le plot partage temps + prix avec
la vue orderflow : naviguer depuis l'un OU l'autre panneau deplace les deux.

Chaque bougie (colonne) :
- un mini CHANDELIER OHLC dans une bande a gauche,
- a droite, une grille de cellules par niveau de prix : barres HORIZONTALES
  proportionnelles au volume (vendeur a gauche du centre, acheteur a droite),
  chiffres bid x ask par-dessus, POC encadre, normalisation par bougie.
- en-tete V (volume) / D (delta) JUSTE au-dessus de la bougie.

Rendu : fonds/barres/grille via QPicture (data coords) ; textes en coords ECRAN
(ne se deforment pas au zoom), affiches seulement si les cellules sont lisibles.
Le pas de regroupement des prix s'ADAPTE a la plage affichee.
"""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPicture

from backend.hub import MarketHub
from .candles import Candle, build_candles, merge_hybrid_candles
from .orderflow_view import BUY, SELL, FlowViewBox, PriceAxis, TzDateAxis

DEFAULT_RES_S = 60.0      # 1 minute par bougie
MAX_ROWS = 46             # nb max de lignes de prix -> tick adaptatif (lisibilite)
IMB_RATIO = 3.0           # un cote >= 3x l'autre => barre en surbrillance (imbalance)
CENTER_GAP = 0.03         # demi-espace au centre (fraction de largeur) pour la meche
MIN_COL_PX = 70           # largeur ecran mini d'une bougie pour afficher les chiffres
MIN_ROW_PX = 12           # hauteur ecran mini d'une cellule pour afficher les chiffres

GRID_PEN = pg.mkPen(QColor(46, 54, 66), width=1, cosmetic=True)
POC_PEN = pg.mkPen(QColor(232, 198, 92), width=1, cosmetic=True)


def _fmt(v: float) -> str:
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:.0f}"
    if v >= 1:
        return f"{v:.1f}"
    return f"{v:.2f}"


class FootprintItem(pg.GraphicsObject):
    def __init__(self) -> None:
        super().__init__()
        self.candles: list[Candle] = []
        self.tick = 0.0
        self.picture = QPicture()
        self._bounds = QRectF()
        # options d'affichage (donnees representees + CARACTERES, controlables
        # independamment) : bid/ask en barres, chiffres bid×ask, en-tete V/D, POC.
        self.show_bars = True       # histogramme bid/ask par niveau
        self.show_numbers = True    # chiffres (caracteres) bid×ask dans les cellules
        self.show_header = True     # en-tete V (volume) / D (delta)
        self.show_poc = False       # mise en evidence du POC (implemente mais OFF par defaut)

    def set_data(self, candles: list[Candle], tick: float) -> None:
        self.candles = candles
        self.tick = tick
        self._generate()
        self.prepareGeometryChange()
        self.update()

    def _generate(self) -> None:
        self.picture = QPicture()
        if not self.candles or self.tick <= 0:
            self._bounds = QRectF()
            return
        tick = self.tick
        p = QPainter(self.picture)
        for c in self.candles:
            w = c.t1 - c.t0
            x0 = c.t0
            xc = x0 + w * 0.5             # centre (separateur bid/ask + meche)
            gap = w * CENTER_GAP          # demi-espace central pour la meche
            maxlen = w * 0.5 - gap        # longueur max d'une barre
            up = c.c >= c.o
            ccol = QColor(*BUY) if up else QColor(*SELL)

            # --- CHANDELIER en ARRIERE-PLAN, subtil, qui englobe le footprint ---
            # corps (open-close) tres discret, sur toute la largeur de la bougie
            body = QColor(ccol); body.setAlphaF(0.10)
            p.setPen(Qt.NoPen); p.setBrush(body)
            p.drawRect(QRectF(x0, min(c.o, c.c), w, max(abs(c.c - c.o), tick * 0.04)))
            obc = QColor(ccol); obc.setAlphaF(0.30)
            op = QPen(obc); op.setCosmetic(True); op.setWidth(1)
            p.setPen(op); p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(x0, min(c.o, c.c), w, max(abs(c.c - c.o), tick * 0.04)))

            rows = set(c.buy) | set(c.sell)
            cmax = 1e-9
            poc, poc_v = None, -1.0
            for r in rows:
                bv, sv = c.buy.get(r, 0.0), c.sell.get(r, 0.0)
                cmax = max(cmax, bv, sv)
                if bv + sv > poc_v:
                    poc_v, poc = bv + sv, r
            for r in rows if self.show_bars else ():     # barres bid/ask optionnelles
                bv, sv = c.buy.get(r, 0.0), c.sell.get(r, 0.0)
                if bv + sv <= 0:
                    continue
                yb = r * tick
                bh = tick * 0.74
                yo = yb + tick * 0.13
                # barres proportionnelles (normalisees par bougie), centrees avec gap
                if sv > 0:
                    ln = sv / cmax * maxlen * 0.98
                    col = QColor(*SELL)
                    col.setAlphaF(0.85 if sv >= IMB_RATIO * max(bv, 1e-9) else 0.5)
                    p.setPen(Qt.NoPen); p.setBrush(col)
                    p.drawRect(QRectF(xc - gap - ln, yo, ln, bh))
                if bv > 0:
                    ln = bv / cmax * maxlen * 0.98
                    col = QColor(*BUY)
                    col.setAlphaF(0.85 if bv >= IMB_RATIO * max(sv, 1e-9) else 0.5)
                    p.setPen(Qt.NoPen); p.setBrush(col)
                    p.drawRect(QRectF(xc + gap, yo, ln, bh))
                # cadre de cellule (grille) + POC
                p.setBrush(Qt.NoBrush)
                p.setPen(POC_PEN if (self.show_poc and r == poc) else GRID_PEN)
                p.drawRect(QRectF(x0, yb, w, tick))

            # --- meche (high-low) dans l'espace central, subtile ---
            wc = QColor(ccol); wc.setAlphaF(0.55)
            wp = QPen(wc); wp.setCosmetic(True); wp.setWidth(1)
            p.setPen(wp)
            p.drawLine(QPointF(xc, c.l), QPointF(xc, c.h))
        p.end()

        tmin = self.candles[0].t0
        tmax = self.candles[-1].t1
        pmin = min(c.l for c in self.candles)
        pmax = max(c.h for c in self.candles)
        self._bounds = QRectF(tmin, pmin, tmax - tmin, max(pmax - pmin, tick))

    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt API)
        return self._bounds

    def paint(self, p: QPainter, *args) -> None:  # noqa: N802 (Qt API)
        p.drawPicture(0, 0, self.picture)
        vb = self.getViewBox()
        if vb is None or not self.candles or self.tick <= 0:
            return
        w0 = self.candles[0].t1 - self.candles[0].t0
        o = vb.mapViewToDevice(QPointF(0.0, 0.0))
        ex = vb.mapViewToDevice(QPointF(w0, 0.0))
        ey = vb.mapViewToDevice(QPointF(0.0, self.tick))
        if o is None or ex is None or ey is None:
            return
        col_px = abs(ex.x() - o.x())
        row_px = abs(ey.y() - o.y())
        (xr0, xr1), (yr0, yr1) = vb.viewRange()
        top_px = vb.mapViewToDevice(QPointF(0.0, yr1)).y()

        p.save()
        p.resetTransform()
        # --- en-tete V / D, juste au-dessus de chaque bougie ---
        if self.show_header and col_px >= 42:
            hf = QFont(); hf.setPixelSize(11); hf.setBold(True)
            p.setFont(hf)
            for c in self.candles:
                if c.t1 < xr0 or c.t0 > xr1:
                    continue
                xc = c.t0 + (c.t1 - c.t0) * 0.5
                # ancrer sur le HAUT REEL du footprint : la cellule la plus haute
                # monte jusqu'a (row_max+1)*tick, soit ~1 tick au-dessus de la
                # meche (c.h) -> sinon l'en-tete V/D chevauche la cellule du haut.
                rows = c.buy.keys() | c.sell.keys()
                top_price = c.h
                if rows:
                    top_price = max(top_price, (max(rows) + 1) * self.tick)
                dh = vb.mapViewToDevice(QPointF(xc, top_price))
                if dh is None:
                    continue
                ytop = max(dh.y() - 32, top_px + 1)   # juste au-dessus du plus haut
                p.setPen(QPen(QColor(170, 182, 196)))
                p.drawText(QRectF(dh.x() - col_px / 2, ytop, col_px, 13),
                           Qt.AlignCenter, f"V {_fmt(c.buy_total + c.sell_total)}")
                p.setPen(QPen(QColor(*BUY) if c.delta >= 0 else QColor(*SELL)))
                p.drawText(QRectF(dh.x() - col_px / 2, ytop + 13, col_px, 13),
                           Qt.AlignCenter, f"D {c.delta:+.2f}")
        # --- chiffres bid x ask par cellule (si assez grand) ---
        if self.show_numbers and col_px >= MIN_COL_PX and row_px >= MIN_ROW_PX:
            f = QFont(); f.setPixelSize(int(min(row_px * 0.6, 12)))
            p.setFont(f)
            gap_px = col_px * CENTER_GAP
            cw = min(col_px * 0.5 - gap_px, 56.0)
            sell_pen = QPen(QColor(255, 175, 175))
            buy_pen = QPen(QColor(165, 248, 210))
            for c in self.candles:
                if c.t1 < xr0 or c.t0 > xr1:
                    continue
                xc = c.t0 + (c.t1 - c.t0) * 0.5
                for r in set(c.buy) | set(c.sell):
                    yc = (r + 0.5) * self.tick
                    if yc < yr0 or yc > yr1:
                        continue
                    bv, sv = c.buy.get(r, 0.0), c.sell.get(r, 0.0)
                    if bv + sv <= 0:
                        continue
                    d = vb.mapViewToDevice(QPointF(xc, yc))
                    if d is None:
                        continue
                    p.setPen(sell_pen)
                    p.drawText(QRectF(d.x() - gap_px - cw, d.y() - row_px / 2, cw, row_px),
                               Qt.AlignRight | Qt.AlignVCenter, _fmt(sv))
                    p.setPen(buy_pen)
                    p.drawText(QRectF(d.x() + gap_px, d.y() - row_px / 2, cw, row_px),
                               Qt.AlignLeft | Qt.AlignVCenter, _fmt(bv))
        p.restore()


class FootprintLayer:
    """Couche FOOTPRINT superposable a l'orderflow : possede un FootprintItem
    (ajoute au plot de l'orderflow, en AVANT-PLAN) + la logique de tick STABLE et
    de reconstruction des bougies depuis les trades. Ce n'est PAS un widget : il est
    pilote par OrderflowView (un seul graphe, un seul viewbox, temps+prix partages).
    """

    def __init__(self, hub: MarketHub, exchange: str, symbol: str,
                 res_s: float = DEFAULT_RES_S) -> None:
        self.hub = hub
        self.exchange = exchange
        self.symbol = symbol
        self.res_s = res_s
        self._cache_key = None
        self._inst_cache = 0.0   # tick d'instrument STABLE (plus petit ecart jamais vu)
        self._tick_val = 0.0     # tick de regroupement courant (avec hysteresis)
        self.item = FootprintItem()

    def _reset(self) -> None:
        self._cache_key = None
        self._inst_cache = 0.0
        self._tick_val = 0.0

    def set_symbol(self, symbol: str) -> None:
        self.symbol = symbol
        self._reset()

    def set_exchange(self, exchange: str) -> None:
        """Change le flux source (swap SPOT<->Futures sans rien redemarrer)."""
        self.exchange = exchange
        self._reset()

    def set_resolution(self, res_s: float) -> None:
        self.res_s = float(res_s)
        self._cache_key = None

    def set_option(self, name: str, on: bool) -> None:
        """Active/desactive une donnee affichee du footprint (show_bars /
        show_numbers / show_header / show_poc) et redessine immediatement."""
        if not hasattr(self.item, name):
            return
        setattr(self.item, name, bool(on))
        self.item.set_data(self.item.candles, self.item.tick)   # regenere + repaint

    def _inst_tick(self) -> float:
        book = self.hub.book(self.exchange, self.symbol)
        asks = np.array([p for p, _ in book.asks])
        if asks.size >= 2:
            gaps = np.diff(np.sort(asks))
            gaps = gaps[gaps > 0]
            if gaps.size:
                # Le vrai tick = plus petit ecart JAMAIS observe (les ecarts sont
                # des multiples du tick). On le memorise de facon monotone : sinon
                # le bruit du carnet (surtout SPOT) ferait varier le tick a chaque
                # frame -> re-bucketing -> TOUTES les chandelles "bougent".
                g = float(gaps.min())
                self._inst_cache = g if self._inst_cache <= 0 else min(self._inst_cache, g)
                return self._inst_cache
        if self._inst_cache > 0:
            return self._inst_cache
        mid = book.mid or (float(asks[0]) if asks.size else 0.0)
        return max(mid * 1e-4, 1e-6) if mid else 1.0

    @staticmethod
    def _nice(x: float) -> float:
        """Plus petit multiple "rond" (1/2/5 x10^k) >= x (>= 1)."""
        if x <= 1.0:
            return 1.0
        k = math.floor(math.log10(x))
        base = 10.0 ** k
        for m in (1.0, 2.0, 5.0, 10.0):
            if m * base >= x:
                return m * base
        return 10.0 * base

    def _tick(self, yspan: float) -> float:
        """Tick de regroupement avec HYSTERESIS : on garde le tick courant tant
        que le nombre de lignes reste confortable (12..MAX_ROWS). On ne le
        recalcule (vers un tick rond) que si on sort vraiment de cette bande
        (vrai zoom). -> la vibration de la plage auto-Y (carnet SPOT large et
        clairseme) ne re-bucketise plus le footprint a chaque frame."""
        inst = self._inst_tick()
        if yspan <= 0:
            return self._tick_val or inst
        cur = self._tick_val
        if cur > 0 and 12.0 <= yspan / cur <= MAX_ROWS:
            return cur                          # encore lisible -> on garde
        target = yspan / (MAX_ROWS * 0.7)       # vise ~0.7*MAX_ROWS lignes (marge)
        mult = self._nice(target / inst) if target > inst else 1.0
        self._tick_val = inst * mult
        return self._tick_val

    def refresh(self, trades: list, t0: float, t1: float, yspan: float) -> None:
        """Reconstruit les bougies pour la fenetre visible (t0,t1 en s, yspan = plage
        de prix). Appele par OrderflowView apres mise a jour de ses axes."""
        tick = self._tick(yspan)
        last_ts = trades[-1].ts if trades else 0
        key = (round(t0, 1), round(t1, 1), len(trades), last_ts,
               round(tick, 8), self.res_s, self.symbol)
        if key == self._cache_key:
            return
        self._cache_key = key
        win = [t for t in trades if t0 <= t.ts / 1000.0 <= t1]
        candles = build_candles(win, self.res_s, tick)
        self.item.set_data(candles, tick)

    def refresh_hybrid(self, entries: list, t0: float, t1: float, yspan: float) -> None:
        """Footprint live HYBRIDE : bougies de plusieurs flux deja recales, sommees.
        `entries` = [(trades_recales, with_vol, basis_token)] avec la REFERENCE (Bitget)
        en premier. Les prix des flux non-ref sont DEJA decales par bucket fige en amont
        (orderflow_view._shift_trades) -> ici on ne fait que construire et sommer (drow=0).
        Le tick (axe Bitget) et le corps OHLC/bid-ask viennent de la reference ;
        `with_vol`=False (Bitget decoche) -> reference sans afficher son volume.
        `basis_token` (basis du bucket courant) invalide le cache quand il bouge."""
        tick = self._tick(yspan)
        cand_entries = []
        total = 0
        last_ts = 0
        sig = []
        for trades, with_vol, token in entries:
            win = [t for t in trades if t0 <= t.ts / 1000.0 <= t1]
            total += len(win)
            if win:
                last_ts = max(last_ts, win[-1].ts)
            sig.append((with_vol, round(token, 3)))
            cand_entries.append((build_candles(win, self.res_s, tick), 0, 0.0, with_vol))
        # cache : depend des trades, des flags ET du basis du bucket courant -> on ne
        # reconstruit pas tant que rien ne bouge.
        key = (round(t0, 1), round(t1, 1), total, last_ts, round(tick, 8),
               self.res_s, self.symbol, tuple(sig))
        if key == self._cache_key:
            return
        self._cache_key = key
        self.item.set_data(merge_hybrid_candles(cand_entries), tick)
