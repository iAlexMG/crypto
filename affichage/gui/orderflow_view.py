"""Vue orderflow facon Bookmap (pyqtgraph).

Gauche  (ORDERFLOW) : heatmap temps x prix de la liquidite resting (alignee sur
                      les niveaux du carnet) + lignes best bid/ask + trades en
                      scatter (aire ~ volume, vert=acheteur, rouge=vendeur).
Droite  (ORDERBOOK) : carnet courant (Size) en histogramme horizontal, meme
                      echelle de prix. Axe des PRIX a droite, a l'intersection.

Live  : le present reste colle au bord droit, l'historique defile vers la gauche.
        - molette         = zoom du TEMPS (reste en live, ancre a droite)
        - molette sur axe  = zoom de cet axe seul (prix a droite / temps en bas)
        - glisser          = pause (inspection libre de l'historique)
        - bouton "Live"    = revenir au temps reel
"""
from __future__ import annotations

import bisect
import math
import time
from collections import deque
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from backend.hub import MarketHub
from .history import HistoryStore
from .arbitrage import find_arbs, TAKER_FEES_BPS, ARB_THRESHOLD_BPS, MIN_NOTIONAL_USD

TZ = ZoneInfo("America/Toronto")   # heure du Quebec

DEFAULT_SPAN = 180.0               # fenetre temps live initiale (s)
# Plus de plafond DUR de plage : on peut degager (zoom out) librement. La densite
# est maitrisee par le filtre AUTO (qui se resserre avec la plage) + MAX_RENDER_COLS
# pour la heatmap. MAX_SPAN reste un garde-fou tres large (~30 j) anti-debordement.
MIN_SPAN, MAX_SPAN = 5.0, 30 * 24 * 3600.0
NBINS_MAX = 600                    # garde-fou resolution heatmap
MAX_RENDER_COLS = 1000             # colonnes temps max de la heatmap (perf)
SAMPLE_HINT_S = 0.5                # pas de temps cible (= cadence sampler) pour la grille
AGG_SPAN_S = 1800.0                # au-dela de cette plage (30 min) -> footprint AGREGE
                                   # en SQL (hors thread) + scatter borne, au lieu des
                                   # trades bruts (sinon gel en zoom arriere). En deca :
                                   # chemin brut, reactif pour le trading live.
AGG_DEBOUNCE_S = 0.25              # zoom arriere : ne LANCE le calcul agrege (~plusieurs
                                   # 100 ms a qq s) qu'une fois la fenetre STABLE depuis ce
                                   # delai -> un geste de molette ne demarre plus un agregat
                                   # par cran (chacun aussitot abandonne). L'ancien footprint
                                   # reste affiche le temps que le nouveau soit pret.
# RESOLUTION FIXE : choisie au menu, le zoom ne la change plus (l'auto-coarsening a
# ete retire). C'est a l'utilisateur de regler la resolution selon le zoom.
# Scatters : UNIQUEMENT la derniere heure reelle (relatif au PRESENT), a toute echelle
# de zoom. Au-dela (navigation historique), pas de scatter : footprint + lignes best
# bid/ask seulement.
LIVE_SCATTER_S = 3600.0
# Mode HYBRIDE : le basis (offset de prix entre exchanges) est estime sur les PRIX TRADES,
# par APPARIEMENT TEMPOREL : chaque trade du flux est apparie au trade Bitget le plus proche
# dans le temps (<=500 ms) et on prend la MEDIANE des ecarts sur BASIS_WINDOW_S. Raison
# (mesuree) : le spread des mids est biaise par le lag asynchrone des carnets ; l'appariement
# temporel annule le timing et donne le vrai offset (~1.4$, mediane robuste). La dispersion
# trade-a-trade restante (~+-5$) est de la microstructure (bounce/lead-lag), irreductible.
BASIS_WINDOW_S = 60.0
BASIS_MATCH_MS = 500    # tolerance d'appariement temporel entre deux trades
# Le basis est FIGE par bucket de temps : calcule une seule fois par bucket puis gele
# pour toujours (seul le bucket courant, encore en formation, est rafraichi). Ainsi les
# donnees deja recalees vers Bitget ne bougent plus. Au 1er passage (ou apres un toggle),
# on backfill une large fenetre (couvre le zoom serre <=30 min ET le scatter derniere heure).
BASIS_BUCKET_MS = 60000          # granularite du basis fige (= bucket rollup, coherence histo)
BASIS_BACKFILL_S = LIVE_SCATTER_S  # fenetre du backfill unique du basis (1 h)
# Au-dela de cet ecart de temps entre 2 points bid/ask consecutifs (et au-dela de
# qq fois le pas nominal), on COUPE la ligne : c'est un trou de donnees (aucun trade)
# -> ne pas le relier par un segment plat/oblique trompeur. = seuil de trou du collecteur.
MIN_BBO_GAP_S = 75.0
PAD_FRAC = 0.0006
RIGHT_MARGIN_FRAC = 0.04          # espace a droite en Live (avant l'axe des prix)
MAX_POINTS = 1200                 # cible de scatters (Auto) pour la plage de REFERENCE
MIN_POINTS = 60                   # plancher de scatters sur tres grande plage (epure)
AUTO_DECAY = 0.7                  # plus grand = se resserre plus vite avec la plage
HARD_CAP = 6000                   # plafond DUR de scatters (meme Auto off) -> rendu fluide

DOT_MIN, DOT_MAX, DOT_SPAN = 6.0, 46.0, 15.0   # points plus discrets (footprint = avant-plan)
TRADE_BRUSH_A = 140                # opacite des points (semi-transparents -> moins envahissants)
TRADE_PEN_A = 90                   # contour sombre subtil
REF_VOL = {"BTCUSDT": 0.5, "ETHUSDT": 5.0}
Y_EMA = 0.2
Y_PAD = 0.15
HEAT_OPACITY = 0.6                 # heatmap semi-transparente -> les trades ressortent

BUY = (0, 220, 130)
SELL = (240, 80, 80)

_HEAT = pg.ColorMap(
    [0.0, 0.15, 0.40, 0.70, 1.0],
    [(8, 12, 22), (18, 38, 80), (20, 110, 130), (70, 180, 90), (245, 220, 80)],
)


_TIME_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200,
               14400, 21600, 43200, 86400, 172800, 604800]


class TzDateAxis(pg.DateAxisItem):
    """Axe temps a l'heure du Quebec. Les graduations sont calculees a partir de
    la PLAGE seule (pas de la largeur en pixels) -> les deux panneaux, lies sur le
    meme intervalle, affichent TOUJOURS exactement les memes graduations/format."""

    def tickValues(self, minVal, maxVal, size):
        span = maxVal - minVal
        if span <= 0:
            return []
        spacing = _TIME_STEPS[-1]
        for s in _TIME_STEPS:
            if span / s <= 7:               # vise ~6-7 graduations majeures
                spacing = s
                break
        first = math.ceil(minVal / spacing) * spacing
        ticks = []
        v = first
        while v <= maxVal:
            ticks.append(v)
            v += spacing
        return [(spacing, ticks)]

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            try:
                dt = datetime.fromtimestamp(v, TZ)
            except (OSError, ValueError, OverflowError):
                out.append("")
                continue
            if spacing >= 86400:
                out.append(dt.strftime("%m-%d"))
            elif spacing >= 60:
                out.append(dt.strftime("%H:%M"))
            else:
                out.append(dt.strftime("%H:%M:%S"))
        return out


class PriceAxis(pg.AxisItem):
    """Axe des prix : labels convertis en $US ou $CA selon le reglage (les
    positions restent en USD ; seul l'affichage change)."""

    owner = None

    def tickStrings(self, values, scale, spacing):
        m = self.owner.price_mult() if self.owner else 1.0
        out = []
        for v in values:
            x = v * m
            out.append(f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}")
        return out


class FlowViewBox(pg.ViewBox):
    """ViewBox custom — zoom independant par axe, fiable partout dans le graphe :
      - molette          => zoom du TEMPS (ancre au present si live)
      - Ctrl + molette   => zoom des PRIX
    """

    owner = None  # injecte apres construction

    def wheelEvent(self, ev, axis=None):
        o = self.owner
        if o is None:
            super().wheelEvent(ev, axis)
            return
        factor = 1.0 - (ev.delta() / 120.0) * 0.15   # molette haut => zoom in
        if ev.modifiers() & Qt.ControlModifier:       # zoom PRIX (les 2 modes)
            ymin, ymax = self.viewRange()[1]
            c, h = (ymin + ymax) / 2, (ymax - ymin) / 2 * factor
            self.setYRange(c - h, c + h, padding=0)
            o.y_manual = True
            ev.accept()
        elif o.follow:                                # LIVE : zoom du temps, ancre a droite
            o.x_span = float(np.clip(o.x_span * factor, MIN_SPAN, MAX_SPAN))
            ev.accept()
        else:                                         # LIBRE : zoom du TEMPS seulement
            # (molette = temps, Ctrl+molette = prix) -> jamais les deux a la fois.
            (x0, x1), _ = self.viewRange()
            try:
                cx = self.mapSceneToView(ev.scenePos()).x()   # centre = curseur
            except Exception:  # noqa: BLE001 - repli : centre de la vue
                cx = (x0 + x1) / 2
            self.setXRange(cx + (x0 - cx) * factor, cx + (x1 - cx) * factor, padding=0)
            ev.accept()


class OrderflowView(QWidget):
    def __init__(self, store: HistoryStore, hub: MarketHub,
                 exchange: str, symbol: str, fx=None,
                 archive=None, backfill=None, book_reader=None,
                 footprint_reader=None, arb_archive=None) -> None:
        super().__init__()
        self.store = store
        self.hub = hub
        self.exchange = exchange      # cle de flux hub du marche AFFICHE
        self.inst_type = "USDT-FUTURES"   # marche affiche (colonne `market` de l'archive)
        self.symbol = symbol
        self.fx = fx                 # taux USD->CAD (FxRate) ou None
        self.archive = archive       # TradeArchive (historique disque) ou None
        self.backfill = backfill     # BackfillManager ou None
        self.book_reader = book_reader  # BookReader (heatmap historique disque) ou None
        self.footprint_reader = footprint_reader  # FootprintReader (footprint agrege) ou None
        self.arb_archive = arb_archive  # ArbArchive (journal d'arbitrage, hybride) ou None
        self.currency = "USD"        # "USD" ou "CAD"
        self.follow = True
        self.y_manual = False
        self.x_span = DEFAULT_SPAN
        self.res_s = 60.0     # resolution des bougies (menu), FIXE (plus d'auto-coarsening)
        self._agg_key = None  # derniere fenetre agregee demandee (debounce molette)
        self._agg_since = 0.0 # instant ou cette fenetre est devenue stable
        self.min_size = 0.0   # filtre volume des trades (n'afficher que size >= seuil)
        self._auto_thresh = 0.0   # seuil de taille STABLE du filtre Auto (hysteresis)
        self.dot_scale = 1.0      # facteur de taille des points (reglage Trades)
        self._yr: tuple[float, float] | None = None
        self._vis_price: tuple[float, float] | None = None  # etendue prix des trades visibles
        self.last_trades: list = []   # trades de la fenetre visible (pour le footprint)
        self._agg_candles: list = []  # dernieres bougies agregees (rollup) -> footprint zoom arriere
        self._agg_bbo = None          # serie bid/ask FINE (trades bruts) en zoom arriere, ou None
        self._ahist: list = []        # cache de la portion archive (rafraichie ~2s)
        self.max_span = MAX_SPAN      # plafond de plage affichee (ajuste par la resolution)
        self._fit_pending = False     # demande d'auto-cadrage prix (apres saut a une date)
        self.auto_filter = True       # filtre auto des trades (moins de points si plage large)
        # --- mode HYBRIDE (agregation recalee sur Bitget ; cf. docs/hybride.md) ---
        self.hybrid = False                       # True = vue hybride active
        self.hybrid_perimeter: list = []          # [(label, feed_key, market_tag)], Bitget en 1er
        self.hybrid_enabled: set[str] = set()     # labels d'exchanges ACTIVES dans l'agregation
        self._stores: dict = {}                   # HistoryStore par flux (heatmap hybride)
        self.hybrid_market = "FUTURES"            # FUTURES/SPOT -> table de frais arbitrage
        self._arbs: list = []                     # opportunites d'arbitrage courantes
        self._arb_log_t: dict = {}                # throttle du log par paire (sell,buy)
        # frais taker + seuil EDITABLES (dialogue) : copie des defauts -> on ne mute pas
        # la table module. find_arbs les recoit en parametres.
        self.fees: dict = {ex: dict(m) for ex, m in TAKER_FEES_BPS.items()}
        self.arb_threshold = ARB_THRESHOLD_BPS
        self.arb_min_notional = MIN_NOTIONAL_USD   # filtre taille (notional exec. mini)
        self._arb_pts: deque = deque(maxlen=4000)  # (ts_s, prix) des opportunites -> marqueurs
        # basis FIGE par flux ET par bucket 60 s : {feed_key: {bucket: offset}}. Un bucket
        # passe est calcule UNE fois puis gele -> les donnees recalees ne bougent plus.
        self._basis_b: dict[str, dict[int, float]] = {}
        self._oref_b: dict[int, float] = {}       # basis Bitget<->ancre par bucket (decouplage)
        self._shift_cache: dict[str, dict] = {}   # {feed: {trade_id: Trade recale FIGE}}
        self._basis_filled = False                # backfill large fait (remis a False sur toggle)
        self._basis_t = 0.0                       # dernier recalcul du basis (throttle ~1 Hz)
        self._ahist_cache: dict = {}              # cache portion archive PAR tag (hybride)

        pg.setConfigOptions(antialias=True, background="#080a10", foreground="#9aa4b2")

        # --- ORDERFLOW (gauche), axe prix a DROITE (sans titre d'axe) ---
        self.vb = FlowViewBox()
        self.vb.owner = self
        self.price_axis = PriceAxis(orientation="right")
        self.price_axis.owner = self
        self.plot = pg.PlotWidget(viewBox=self.vb, axisItems={
            "bottom": TzDateAxis(orientation="bottom"),
            "right": self.price_axis,
        })
        self.plot.hideAxis("left")
        self.plot.showAxis("right")
        self.plot.showGrid(x=True, y=True, alpha=0.18)   # grille delicate mais visible
        self.vb.sigRangeChangedManually.connect(self._on_manual_range)

        self.img = pg.ImageItem()
        self.img.setLookupTable(_HEAT.getLookupTable(0.0, 1.0, 256))
        self.img.setOpacity(HEAT_OPACITY)
        self.img.setZValue(0)
        self.plot.addItem(self.img)

        self.bid_line = pg.PlotCurveItem(pen=pg.mkPen(BUY + (180,), width=1))
        self.ask_line = pg.PlotCurveItem(pen=pg.mkPen(SELL + (180,), width=1))
        self.bid_line.setZValue(10); self.ask_line.setZValue(10)
        self.plot.addItem(self.bid_line); self.plot.addItem(self.ask_line)

        dot_pen = pg.mkPen(8, 10, 16, TRADE_PEN_A)
        self.buys = pg.ScatterPlotItem(pen=dot_pen, brush=pg.mkBrush(*BUY, TRADE_BRUSH_A), pxMode=True)
        self.sells = pg.ScatterPlotItem(pen=dot_pen, brush=pg.mkBrush(*SELL, TRADE_BRUSH_A), pxMode=True)
        self.buys.setZValue(20); self.sells.setZValue(20)
        self.plot.addItem(self.sells); self.plot.addItem(self.buys)

        # --- couche FOOTPRINT superposee, en AVANT-PLAN (Z le plus haut) ---
        # import local : footprint_view importe deja orderflow_view (eviter le
        # cycle a l'import du module).
        from .footprint_view import FootprintLayer
        self.fp = FootprintLayer(hub, exchange, symbol)
        self.fp.set_resolution(self.res_s)
        self.plot.addItem(self.fp.item)
        # marqueurs d'ARBITRAGE (hybride) : etoiles dorees au prix/temps de l'opportunite,
        # AU-DESSUS de tout (Z max). Vide hors hybride.
        self.arb_marks = pg.ScatterPlotItem(symbol="star", size=16, pxMode=True,
                                            pen=pg.mkPen(20, 20, 10, 220),
                                            brush=pg.mkBrush(255, 210, 40, 230))
        self.arb_marks.setZValue(100)
        self.plot.addItem(self.arb_marks)
        # COUCHES superposees (le carnet DOM est un panneau lateral, gere a part).
        # bid/ask = contexte de la heatmap -> regroupes avec elle.
        self._layers = {
            "footprint": (self.fp.item,),
            "heatmap": (self.img, self.bid_line, self.ask_line),
            "trades": (self.buys, self.sells),
        }
        self._layer_order = ["heatmap", "trades", "footprint"]   # bas -> haut (avant)
        self._apply_z_order()

        # --- LEGENDE HYBRIDE : petit overlay flottant DANS le graphe (coin haut
        # gauche), une case a cocher par exchange du perimetre -> selection directe
        # des flux agreges. Cachee hors mode hybride. ---
        self._legend = QWidget(self.plot)
        self._legend.setStyleSheet(
            "QWidget{background:rgba(13,17,23,200);border:1px solid #2b3340;}"
            "QLabel{border:none;color:#8b949e;font-weight:600;font-size:11px;}"
            "QCheckBox{border:none;color:#c9d1d9;font-size:11px;}")
        self._legend_lay = QVBoxLayout(self._legend)
        self._legend_lay.setContentsMargins(8, 5, 10, 6)
        self._legend_lay.setSpacing(2)
        _lt = QLabel("Hybride")
        self._legend_lay.addWidget(_lt)
        self._legend_boxes: dict[str, QCheckBox] = {}
        self._legend.hide()

        # --- ORDERBOOK courant (droite), echelle prix synchronisee ---
        # IMPORTANT alignement : meme geometrie verticale que la heatmap (pas de
        # titre, axe du bas sans label) -> un prix tombe a la MEME hauteur dans
        # les deux panneaux.
        self.dom = pg.PlotWidget()
        self.dom.setMaximumWidth(140)
        self.dom.showGrid(x=True, y=False, alpha=0.10)
        self.dom.setYLink(self.plot)
        self.dom.hideAxis("left")
        self.dom_bids = pg.BarGraphItem(x0=0, y=[], height=0, width=[], brush=pg.mkBrush(*BUY, 150), pen=None)
        self.dom_asks = pg.BarGraphItem(x0=0, y=[], height=0, width=[], brush=pg.mkBrush(*SELL, 150), pen=None)
        self.dom.addItem(self.dom_bids); self.dom.addItem(self.dom_asks)

        # hauteur d'axe du bas identique des deux cotes -> bords inferieurs alignes
        self.plot.getAxis("bottom").setHeight(34)
        self.dom.getAxis("bottom").setHeight(34)

        # barre d'etat AU-DESSUS des graphes (et non un titre sur le graphe, qui
        # decalerait verticalement la heatmap par rapport au DOM).
        self.status = QLabel("")
        self.status.setStyleSheet("color:#c9d1d9;font-size:12px;padding:2px 4px;")
        self.status.setFixedHeight(20)   # = en-tete footprint -> traces alignes en Y
        # horloge temps reel (heure du Quebec, avec les secondes), a droite de l'en-tete
        self.clock = QLabel("")
        self.clock.setStyleSheet("color:#8b949e;font-size:12px;padding:2px 8px;"
                                 "font-family:Consolas,monospace;")
        self.clock.setFixedHeight(20)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(self.status)
        header.addStretch(1)
        header.addWidget(self.clock)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.plot, 1)
        row.addWidget(self.dom)

        # devise $US/$CA, DISCRETE, en bas a droite (sous l'axe des prix)
        cur_row = QHBoxLayout()
        cur_row.setContentsMargins(0, 0, 8, 0)
        cur_row.setSpacing(2)
        cur_row.addStretch(1)
        _cur_css = ("QPushButton{padding:1px 6px;font-size:10px;color:#6e7681;"
                    "background:transparent;border:none;}"
                    "QPushButton:checked{color:#c9d1d9;}")
        self._cur_btns = {}
        for code, label in (("USD", "$US"), ("CAD", "$CA")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(code == "USD")
            b.setStyleSheet(_cur_css)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=code: self._pick_currency(c))
            self._cur_btns[code] = b
            cur_row.addWidget(b)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addLayout(header)
        outer.addLayout(row, 1)
        outer.addLayout(cur_row)

    # -- interactions : 2 modes -------------------------------------------
    # follow=True  -> mode LIVE  : colle au present, l'axe prix suit le carnet
    # follow=False -> mode LIBRE : l'utilisateur navigue librement dans l'historique
    def _on_manual_range(self) -> None:
        # tout glissement/pan a la souris fait passer en mode LIBRE
        if self.follow:
            self.follow = False
        # plafonne la plage affichee a max_span (anti-lag sur tres long historique)
        (xmin, xmax), _ = self.vb.viewRange()
        if xmax - xmin > self.max_span:
            self.vb.setXRange(xmax - self.max_span, xmax, padding=0)

    def set_free(self) -> None:
        self.follow = False

    def go_live(self) -> None:
        # CONSERVE la periode d'affichage courante : revenir en Live recolle au
        # present sans changer la duree visible (ex. 15 min reste 15 min).
        (x0, x1), _ = self.vb.viewRange()
        span = x1 - x0
        if span > 0:
            self.x_span = float(np.clip(span, MIN_SPAN, self.max_span))
        self.follow = True
        self.y_manual = False
        self._yr = None
        self._fit_pending = False

    def set_max_span(self, span: float) -> None:
        self.max_span = max(MIN_SPAN, float(span))

    def set_resolution(self, res_s: float) -> None:
        self.res_s = max(1.0, float(res_s))
        self.fp.set_resolution(self.res_s)

    def _apply_z_order(self) -> None:
        """Assigne les Z selon l'ordre courant (bas -> haut = arriere -> avant)."""
        for i, name in enumerate(self._layer_order):
            for it in self._layers[name]:
                it.setZValue((i + 1) * 10)

    def set_layer_visible(self, name: str, on: bool) -> None:
        """Affiche/masque une couche superposee (footprint/heatmap/trades)."""
        for it in self._layers.get(name, ()):
            it.setVisible(on)

    def set_layer_opacity(self, name: str, value: float) -> None:
        """Regle la transparence d'une couche (0..1)."""
        v = float(np.clip(value, 0.0, 1.0))
        for it in self._layers.get(name, ()):
            it.setOpacity(v)

    def move_layer(self, name: str, to_front: bool) -> None:
        """Avance/recule une couche dans l'empilement (avant/arriere)."""
        order = self._layer_order
        if name not in order:
            return
        i = order.index(name)
        j = i + 1 if to_front else i - 1
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            self._apply_z_order()

    def set_dom_visible(self, on: bool) -> None:
        self.dom.setVisible(on)

    def set_footprint_option(self, name: str, on: bool) -> None:
        """Donnees affichees du footprint (barres bid/ask, chiffres, en-tete, POC)."""
        self.fp.set_option(name, on)

    def goto(self, t_start: float) -> None:
        """Saute a une date precise (epoch s) en mode Libre, et recadre le prix
        sur les donnees de cette fenetre des qu'elles sont chargees."""
        self.follow = False
        self.y_manual = False
        self._fit_pending = True
        self.vb.setXRange(t_start, t_start + self.x_span, padding=0)

    def set_symbol(self, symbol: str) -> None:
        self.symbol = symbol
        self.fp.set_symbol(symbol)
        self.go_live()

    def set_store(self, store: HistoryStore) -> None:
        """Rebranche la vue sur un autre historique (swap SPOT<->Futures) sans
        rien perdre : chaque marche garde sa propre heatmap en memoire."""
        self.store = store
        self.reset()

    def set_market(self, exchange: str, inst_type: str) -> None:
        """Swap SPOT<->Futures cote AFFICHAGE : on change juste la cle de flux lue
        et le marche d'archive. Les deux marches continuent d'etre captes."""
        self.hybrid = False
        self._legend.hide()
        self.exchange = exchange
        self.inst_type = inst_type
        self._akey = None        # invalide le cache archive
        self.fp.set_exchange(exchange)
        self.reset()

    def set_hybrid(self, perimeter: list, base_key: str, base_tag: str, store,
                   stores: dict | None = None, market: str = "FUTURES") -> None:
        """Active le mode HYBRIDE. `perimeter` = [(label, feed_key, market_tag)] avec
        Bitget en 1er (reference de prix). `stores` = HistoryStore PAR flux (deja
        echantillonnes) -> permet de fusionner la heatmap (footprint + DOM + heatmap
        hybrides ; bid/ask = reference Bitget). cf. docs/hybride.md."""
        self.hybrid = True
        self.hybrid_perimeter = perimeter
        self.hybrid_enabled = {label for label, _, _ in perimeter}
        self._stores = stores or {}        # HistoryStore par flux (heatmap hybride)
        self.hybrid_market = market        # FUTURES/SPOT -> frais taker de l'arbitrage
        self._arbs = []
        self._basis_b = {}                 # repart d'une carte de basis vierge
        self._oref_b = {}
        self._shift_cache = {}
        self._basis_filled = False
        self.exchange = base_key       # reference (Bitget) pour book/bid-ask/heatmap/DOM
        self.inst_type = base_tag
        self.store = store
        self._akey = None
        self.fp.set_exchange(base_key)
        self._build_legend()
        self.reset()

    def _build_legend(self) -> None:
        """(Re)construit les cases de la legende depuis le perimetre. Le 1er exchange
        (Bitget, reference) est verrouille (toujours actif)."""
        for cb in self._legend_boxes.values():
            self._legend_lay.removeWidget(cb)
            cb.setParent(None)
        self._legend_boxes.clear()
        for i, (label, _key, _tag) in enumerate(self.hybrid_perimeter):
            tag_txt = f"{label}  (réf.)" if i == 0 else label
            cb = QCheckBox(tag_txt)
            cb.setChecked(label in self.hybrid_enabled)
            # Bitget reste la REFERENCE de prix (axe/recalage) meme decoche : decocher
            # masque seulement SON volume du footprint (les autres restent recales dessus).
            cb.toggled.connect(lambda on, l=label: self._toggle_exchange(l, on))
            self._legend_lay.addWidget(cb)
            self._legend_boxes[label] = cb
        self._legend.adjustSize()
        self._legend.move(12, 12)
        self._legend.show()
        self._legend.raise_()

    def _toggle_exchange(self, label: str, on: bool) -> None:
        if on:
            self.hybrid_enabled.add(label)
        else:
            self.hybrid_enabled.discard(label)
        # un flux nouvellement active n'a pas son basis passe -> forcer un backfill large
        self._basis_filled = False
        self.refresh()

    def set_min_size(self, value: float) -> None:
        self.min_size = max(0.0, float(value))

    def set_dot_scale(self, value: float) -> None:
        self.dot_scale = max(0.1, float(value))

    def price_mult(self) -> float:
        if self.currency == "CAD" and self.fx is not None:
            return float(self.fx.usd_cad)
        return 1.0

    def set_currency(self, currency: str) -> None:
        self.currency = "CAD" if currency.upper() == "CAD" else "USD"
        self.price_axis.picture = None   # invalide le cache -> relabel
        self.price_axis.update()

    def _pick_currency(self, code: str) -> None:
        """Bascule devise depuis les boutons discrets sous l'axe des prix."""
        self.set_currency(code)
        for c, b in self._cur_btns.items():
            b.setChecked(c == self.currency)

    def reset(self) -> None:
        """Apres un changement de marche (SPOT/Futures) : on repart a zero."""
        self._yr = None
        self.go_live()

    # -- helpers -----------------------------------------------------------
    def _book_tick(self, book) -> float:
        """Pas de prix reel = plus petit ecart entre niveaux consecutifs d'un
        meme cote (= le tick de l'instrument)."""
        asks = np.array([p for p, _ in book.asks])
        if asks.size < 2:
            return 0.0
        gaps = np.diff(np.sort(asks))
        gaps = gaps[gaps > 0]
        return float(gaps.min()) if gaps.size else 0.0

    def _grid(self, lo: float, hi: float, tick: float):
        """Grille de prix COMMUNE a la heatmap et au DOM -> alignement parfait.
        Renvoie (tick_eff, row0, nb, y0, hauteur)."""
        if tick <= 0:
            tick = (hi - lo) / 200.0
        row_lo = int(np.floor(lo / tick))
        row_hi = int(np.ceil(hi / tick))
        nb = row_hi - row_lo
        if nb > NBINS_MAX:                      # agrege si trop de niveaux
            k = int(np.ceil(nb / NBINS_MAX))
            tick *= k
            row_lo = int(np.floor(lo / tick))
            row_hi = int(np.ceil(hi / tick))
            nb = row_hi - row_lo
        nb = max(nb, 1)
        return tick, row_lo, nb, row_lo * tick, nb * tick

    @staticmethod
    def _bin(prices: np.ndarray, sizes: np.ndarray, tick: float, row0: int, nb: int) -> np.ndarray:
        out = np.zeros(nb, dtype=np.float32)
        rows = np.round(prices / tick).astype(np.int64) - row0
        m = (rows >= 0) & (rows < nb)
        if m.any():
            np.add.at(out, rows[m], sizes[m])   # somme des tailles dans chaque bin
        return out

    def _auto_y(self, book) -> tuple[float, float] | None:
        if not book.bids or not book.asks:
            return None
        lo, hi = book.bids[-1][0], book.asks[-1][0]
        if hi <= lo:
            return None
        # ENGLOBER l'action de prix VISIBLE : sinon, en zoom arriere (Live), l'axe
        # Y reste colle au prix courant et tout l'historique a un prix different
        # tombe HORS de l'ecran (= "pas d'historique"). Etendue mise en cache par
        # _draw_trades (frame precedente -> 1 frame de retard, sans cout).
        if self._vis_price is not None:
            lo = min(lo, self._vis_price[0])
            hi = max(hi, self._vis_price[1])
        pad = (hi - lo) * Y_PAD
        target = (lo - pad, hi + pad)
        if self._yr is None:
            self._yr = target
        else:
            self._yr = (self._yr[0] + (target[0] - self._yr[0]) * Y_EMA,
                        self._yr[1] + (target[1] - self._yr[1]) * Y_EMA)
        return self._yr

    # -- rendu (QTimer) ----------------------------------------------------
    def refresh(self) -> None:
        hist = self.store.hist[self.symbol]
        latest = hist.t_last
        # Ancrer le bord droit du Live sur l'activite REELLE : le dernier TRADE
        # prime l'echantillon de carnet. Sinon, si le flux carnet se fige (ex.
        # resync Binance Futures bloquee) pendant que les trades continuent,
        # t_last (pilote par le carnet) reste en arriere et la chandelle EN COURS
        # sort de l'ecran -> le Live ne montre plus la bougie courante au complet.
        tts = self.hub.last_trade_ts(self.exchange, self.symbol)
        if tts:
            latest = max(latest or 0.0, tts / 1000.0)
        if not latest:
            return
        book = self.hub.book(self.exchange, self.symbol)

        # RESOLUTION = celle du menu, FIXE (plus d'auto-coarsening). L'utilisateur
        # choisit la resolution ; le zoom ne la change plus.
        eff_res = self.res_s
        self.fp.res_s = eff_res        # le footprint (tight + agrege) suit la resolution

        if self.follow:
            # petit espace a droite -> le present n'est pas colle a l'axe des prix.
            # Le bord droit doit englober TOUTE la bougie en cours (qui s'etend
            # jusqu'a la prochaine borne de resolution), sinon le Live coupe la
            # chandelle courante du footprint.
            margin = self.x_span * RIGHT_MARGIN_FRAC
            candle_end = math.floor(latest / eff_res) * eff_res + eff_res
            t1 = max(latest, candle_end) + margin
            t0 = t1 - self.x_span
            self.vb.setXRange(t0, t1, padding=0)
            if not self.y_manual:
                yr = self._auto_y(book)
                if yr:
                    self.vb.setYRange(*yr, padding=0)
            yr = tuple(self.vb.viewRange()[1])
        else:
            t0, t1 = self.vb.viewRange()[0]
            yr = tuple(self.vb.viewRange()[1])

        # plafond d'affichage : on ne rend jamais plus de max_span (anti-lag)
        t0 = max(t0, t1 - self.max_span)

        # GRILLE DE PRIX COMMUNE -> heatmap et orderbook partagent exactement les
        # memes niveaux verticaux (point clef : alignement parfait des deux).
        lo, hi = yr
        tick = self._book_tick(book)
        grid = self._grid(lo, hi, tick) if hi > lo else None

        # cols = carnet de REFERENCE Bitget (mem+disque) : sert au bid/ask (reconstruction)
        # et, hors hybride, au remplissage de la heatmap.
        cols = self._heatmap_cols(hist, t0, t1)
        if self.hybrid:
            self._draw_heatmap_hybrid(grid, t0, t1)   # somme recalee des flux du perimetre
        else:
            self._draw_heatmap(cols, grid, t0, t1)
        # ZOOM ARRIERE (plage large) -> footprint AGREGE en SQL (hors thread) +
        # scatter BORNE : cout independant du nombre de trades, pas de gel.
        # ZOOM SERRE -> chemin brut (reactif pour le footprint live).
        wide = (t1 - t0) > AGG_SPAN_S and self.archive is not None \
            and self.footprint_reader is not None
        if wide:
            self._draw_flow_agg(t0, t1, yr)
        elif self.hybrid:
            # footprint live HYBRIDE : bougies de tous les flux actifs, recalees sur
            # Bitget, sommees. last_trades / bid-ask = reference Bitget (entries[0]).
            entries, base_trades = self._hybrid_live_entries(t0, t1)
            self.last_trades = base_trades
            if base_trades:
                pr = np.fromiter((t.price for t in base_trades), np.float64, len(base_trades))
                self._vis_price = (float(pr.min()), float(pr.max()))
            self._draw_scatter_lasthour(t0, t1)
            if self.fp.item.isVisible():
                self.fp.refresh_hybrid(entries, t0, t1, yr[1] - yr[0])
        else:
            self._draw_trades(t0, t1)        # met a jour self.last_trades
            if self.fp.item.isVisible():
                self.fp.refresh(self.last_trades, t0, t1, yr[1] - yr[0])
        # bid/ask APRES le flow : utilise last_trades / _agg_candles fraichement
        # mis a jour pour reconstruire les lignes la ou le carnet manque.
        self._draw_bbo(cols, t0, t1, wide)
        self._draw_dom(book, grid)
        if self.hybrid:
            self._run_arbitrage()        # opportunites live (prix bruts) + log
        self._draw_arb_markers(t0, t1)   # marqueurs (vide hors hybride)
        self._update_title(book)
        self.clock.setText(datetime.now(TZ).strftime("%H:%M:%S"))

        # apres un saut a une date : recadre le prix sur l'etendue visible
        if self._fit_pending and self._vis_price is not None:
            lo, hi = self._vis_price
            if hi > lo:
                pad = (hi - lo) * 0.1
                self.vb.setYRange(lo - pad, hi + pad, padding=0)
                self.y_manual = True
                self._fit_pending = False

    def _heatmap_cols(self, hist, t0: float, t1: float) -> list:
        """Colonnes de heatmap pour [t0, t1] : l'historique EN MEMOIRE (~2 h
        recentes) complete par le DISQUE (books.db) pour la partie plus ancienne.

        La lecture disque est BORNEE (~MAX_RENDER_COLS colonnes reparties) et
        ASYNCHRONE (BookReader, hors thread Qt) -> jamais de gel, meme si
        books.db est enorme. Tant que le disque charge, on affiche la memoire ;
        la partie historique se complete une fraction de seconde apres."""
        mem = hist.visible(t0, t1)
        oldest = hist.t_first
        # rien a completer si pas de lecteur disque, ou si la memoire couvre deja
        # tout le bord gauche de la fenetre.
        if self.book_reader is None or (oldest is not None and oldest <= t0):
            return mem
        hi = oldest if oldest is not None else t1            # borne de raccord
        key = self.book_reader.key(self.inst_type, self.symbol,
                                   int(t0 * 1000), int(hi * 1000))
        self.book_reader.request(key)                        # non bloquant
        disk = self.book_reader.get(key)                     # [] si pas encore pret
        if not disk:
            return mem
        # disque (plus ancien) puis memoire ; les deux sont tries par ts croissant
        return disk + mem

    def _draw_heatmap(self, cols: list, grid, t0: float, t1: float) -> None:
        if not cols or grid is None:
            self.img.clear()
            return
        tick, row0, nb, y0, height = grid
        # GRILLE DE TEMPS UNIFORME : chaque colonne est placee dans le bin temps
        # correspondant a SON timestamp reel. Sans cela, setRect repartit les
        # colonnes uniformement et, des qu'il y a un TROU d'echantillonnage
        # (reconnexion, ou marche gele pendant une excursion SPOT<->Futures),
        # les colonnes voisines sont etirees -> fausses "bandes". Ici un trou
        # reste un trou (bins vides = sombres), et l'axe du temps est fidele.
        span = max(t1 - t0, 1e-3)
        nt = int(min(MAX_RENDER_COLS, max(1.0, span / (SAMPLE_HINT_S))))
        dt = span / nt
        arr = np.zeros((nt, nb), dtype=np.float32)
        for c in cols:
            j = int((c.ts - t0) / dt)
            if 0 <= j < nt:
                arr[j] = self._bin(c.prices, c.sizes, tick, row0, nb)   # dernier gagne
        np.log1p(arr, out=arr)
        pos = arr[arr > 0]
        vmax = float(np.quantile(pos, 0.95)) if pos.size else 1.0
        self.img.setImage(arr, autoLevels=False, levels=(0.0, max(vmax, 1e-6)))
        self.img.setRect(pg.QtCore.QRectF(t0, y0, span, height))

    def _draw_heatmap_hybrid(self, grid, t0: float, t1: float) -> None:
        """Heatmap HYBRIDE (live, ~2 h en memoire) : pour chaque flux ACTIF, on binne ses
        colonnes de carnet RECALEES sur l'axe Bitget (basis FIGE du bucket de chaque
        colonne) -> last-wins par bin temps (un snapshot par bin) ; puis on SOMME les flux.
        La heatmap est une intensite de liquidite (pas de separation bid/ask) -> aucun
        croisement a gerer. Tous les flux sont deja echantillonnes (self._stores).
        L'historique profond (disque, books.db multi-flux) reste a faire."""
        if grid is None:
            self.img.clear()
            return
        tick, row0, nb, y0, height = grid
        span = max(t1 - t0, 1e-3)
        nt = int(min(MAX_RENDER_COLS, max(1.0, span / SAMPLE_HINT_S)))
        dt = span / nt
        arr = np.zeros((nt, nb), dtype=np.float32)
        for i, (label, key, _tag) in enumerate(self.hybrid_perimeter):
            if label not in self.hybrid_enabled or key not in self._stores:
                continue
            hist = self._stores[key].hist.get(self.symbol)
            if hist is None:
                continue
            feed = np.zeros((nt, nb), dtype=np.float32)   # last-wins par bin pour CE flux
            basis = self._basis_b.get(key, {})
            for c in hist.visible(t0, t1):
                j = int((c.ts - t0) / dt)
                if not (0 <= j < nt):
                    continue
                s = 0.0 if i == 0 else float(basis.get(int(c.ts * 1000) // BASIS_BUCKET_MS, 0.0))
                feed[j] = self._bin(c.prices + s, c.sizes, tick, row0, nb)
            arr += feed                                   # somme des flux
        np.log1p(arr, out=arr)
        pos = arr[arr > 0]
        vmax = float(np.quantile(pos, 0.95)) if pos.size else 1.0
        self.img.setImage(arr, autoLevels=False, levels=(0.0, max(vmax, 1e-6)))
        self.img.setRect(pg.QtCore.QRectF(t0, y0, span, height))

    def _draw_bbo(self, cols: list, t0: float, t1: float, wide: bool) -> None:
        """Lignes best bid/ask CONTINUES sur toute la fenetre. Le carnet REEL
        (memoire + books.db) prime la ou il existe ; PARTOUT ailleurs (avant le
        carnet ET dans les TROUS entre deux zones de carnet) on RECONSTRUIT bid/ask
        depuis les trades (zoom serre) ou le rollup (zoom arriere) : un acheteur
        agresseur a tape l'ask, un vendeur le bid."""
        n = len(cols)
        if n:
            cts = np.fromiter((c.ts for c in cols), np.float64, n)
            cbid = np.fromiter((c.bid for c in cols), np.float64, n)
            cask = np.fromiter((c.ask for c in cols), np.float64, n)
            order = np.argsort(cts, kind="stable")   # searchsorted exige un tri
            cts, cbid, cask = cts[order], cbid[order], cask[order]
        else:
            cts = cbid = cask = np.empty(0, np.float64)
        # reconstruction sur TOUTE la fenetre, puis fusion : la reconstruction n'est
        # gardee que dans les VRAIS trous du carnet (pas par-dessus une zone reelle).
        rts, rbid, rask, step = self._reconstruct_bbo(t0, t1, wide)
        bts, bbid, bask = self._merge_bbo(cts, cbid, cask, rts, rbid, rask)
        if bts.size < 2:
            self.bid_line.clear(); self.ask_line.clear()
            return
        # COUPER la ligne sur les VRAIS TROUS de DONNEES (aucun trade -> aucun point) :
        # sinon connect relie deux points distants par un segment plat/oblique trompeur
        # (cf. ligne plate au gros dezoom). Seuil = qq fois le pas nominal des points
        # (adapte au zoom), plancher anti-bruit. `step` vient de la reconstruction (la
        # source la plus grossiere) ; repli sur l'espacement du carnet si pas de recon.
        if step <= 0:
            step = self._median_step(bts)
        gap = max(2.5 * step, MIN_BBO_GAP_S)
        # connect array (0/1) -> coupe sur un grand ecart de temps OU un NaN (cote pas
        # encore trade). nan_to_num : les points non connectes ne sont de toute facon
        # pas dessines, le 0 ne s'affiche jamais.
        self.bid_line.setData(bts, np.nan_to_num(bbid), connect=self._line_connect(bts, bbid, gap))
        self.ask_line.setData(bts, np.nan_to_num(bask), connect=self._line_connect(bts, bask, gap))

    @staticmethod
    def _median_step(ts: np.ndarray) -> float:
        """Pas nominal (s) entre points = médiane des écarts > 0. 0 si < 2 points."""
        if ts.size < 2:
            return 0.0
        d = np.diff(ts)
        d = d[d > 0]
        return float(np.median(d)) if d.size else 0.0

    @staticmethod
    def _line_connect(ts: np.ndarray, y: np.ndarray, gap: float) -> np.ndarray:
        """Tableau connect (0/1, longueur n) : 1 = relie ce point au suivant. On coupe
        si l'écart de temps dépasse `gap` (trou de données) ou si l'un des deux côtés
        est NaN (côté pas encore tradé)."""
        n = ts.size
        c = np.zeros(n, dtype=np.int32)
        if n > 1:
            ok = (np.diff(ts) <= gap) & np.isfinite(y[:-1]) & np.isfinite(y[1:])
            c[:-1] = ok.astype(np.int32)
        return c

    @staticmethod
    def _merge_bbo(cts, cbid, cask, rts, rbid, rask):
        """Fusionne carnet REEL (cts) et reconstruction (rts) en une serie continue
        triee par temps. La reconstruction n'est conservee que dans les TROUS du
        carnet : on jette tout point reconstruit a moins de `tol` d'un point de
        carnet (tol = ~espacement reel du carnet -> on ne double pas dans une zone,
        mais on comble les ecarts entre zones). Sans carnet -> reconstruction seule ;
        sans reconstruction -> carnet seul."""
        if cts.size == 0:
            return rts, rbid, rask
        if rts.size == 0:
            return cts, cbid, cask
        if cts.size >= 2:
            d = np.diff(cts)
            d = d[d > 0]
            med = float(np.median(d)) if d.size else 5.0
            tol = float(np.clip(3.0 * med, 1.0, 30.0))   # seuil "dans une zone"
        else:
            tol = 5.0
        pos = np.searchsorted(cts, rts)
        lo = np.clip(pos - 1, 0, cts.size - 1)
        hi = np.clip(pos, 0, cts.size - 1)
        dist = np.minimum(np.abs(rts - cts[lo]), np.abs(rts - cts[hi]))
        keep = dist > tol                                # uniquement les vrais trous
        mts = np.concatenate([cts, rts[keep]])
        mbid = np.concatenate([cbid, rbid[keep]])
        mask = np.concatenate([cask, rask[keep]])
        order = np.argsort(mts, kind="stable")
        return mts[order], mbid[order], mask[order]

    def _reconstruct_bbo(self, t0: float, t1: float, wide: bool):
        """(ts, bid, ask, step) reconstruits pour [t0, t1] : report (carry-forward)
        d'un cote tant qu'il n'a pas re-trade ; NaN avant le 1er echange de ce cote
        (la ligne demarre la). Zoom serre = trades bruts de la fenetre (fin). Zoom
        arriere = serie FINE (trades bruts regroupes, calculee hors thread par le
        footprint_reader) ; repli sur le pas 60 s du rollup (bougies) si la plage
        depasse le plafond du calcul fin. `step` = pas nominal des points (pour la
        detection des trous a l'affichage)."""
        if wide:
            if self._agg_bbo is not None:
                ts, bid, ask = self._agg_bbo
                m = (ts >= t0) & (ts <= t1)
                ts, bid, ask = ts[m], bid[m], ask[m]
            else:
                ts, bid, ask = self._bbo_from_candles(t0, t1)
        else:
            ts, bid, ask = self._bbo_from_trades(t0, t1)
        return ts, bid, ask, self._median_step(ts)

    def _bbo_from_candles(self, t0: float, t1: float):
        candles = [c for c in self._agg_candles if c.t1 >= t0 and c.t0 <= t1]
        n = len(candles)
        if n == 0:
            e = np.empty(0, np.float64)
            return e, e, e
        ts = np.empty(n, np.float64); bid = np.empty(n, np.float64); ask = np.empty(n, np.float64)
        lb = la = math.nan
        for i, c in enumerate(candles):     # bougies deja triees par bucket
            if c.bid is not None:
                lb = c.bid
            if c.ask is not None:
                la = c.ask
            ts[i] = (c.t0 + c.t1) * 0.5
            bid[i] = lb; ask[i] = la
        return ts, bid, ask

    def _bbo_from_trades(self, t0: float, t1: float):
        trades = self.last_trades
        n = len(trades)
        if n == 0:
            e = np.empty(0, np.float64)
            return e, e, e
        ts = np.fromiter((t.ts for t in trades), np.float64, n) / 1000.0
        price = np.fromiter((t.price for t in trades), np.float64, n)
        is_buy = np.fromiter((t.side == "buy" for t in trades), bool, n)
        keep = (ts >= t0) & (ts <= t1)
        ts, price, is_buy = ts[keep], price[keep], is_buy[keep]
        if ts.size == 0:
            e = np.empty(0, np.float64)
            return e, e, e
        ask = np.where(is_buy, price, np.nan)   # acheteur agresseur = ask touche
        bid = np.where(~is_buy, price, np.nan)  # vendeur agresseur = bid touche
        self._ffill(ask); self._ffill(bid)      # report jusqu'au prochain trade du cote
        return ts, bid, ask

    @staticmethod
    def _ffill(a: np.ndarray) -> None:
        """Comble les NaN par la derniere valeur connue (en place). Les NaN de TETE
        (avant la 1ere valeur) restent NaN -> la ligne ne demarre qu'au 1er trade."""
        idx = np.where(~np.isnan(a), np.arange(a.size), 0)
        np.maximum.accumulate(idx, out=idx)
        a[:] = a[idx]

    def _feed_trades(self, key: str, tag: str, t0: float, t1: float) -> list:
        """Trades visibles d'UN flux (cle hub `key`, tag archive `tag`) : live (buffer
        memoire) + archive historique si la fenetre remonte avant le live. Portion
        archive mise en cache PAR tag (~2 s) -> footprint stable a 10 fps. Generalise
        l'ancien _visible_trades pour permettre l'agregation hybride multi-flux."""
        t0_ms, t1_ms = int(t0 * 1000), int(t1 * 1000)
        live = self.hub.trades_since(key, self.symbol, t0_ms)
        if self.archive is None:
            return live
        live_oldest = live[0].ts if live else t1_ms
        if live_oldest <= t0_ms:
            return live                                   # le live couvre tout
        hi = min(live_oldest, t1_ms)
        akey = (tag, self.symbol, t0_ms // 1000, hi // 1000)
        now = time.monotonic()
        cache = self._ahist_cache.get(tag)
        if cache is None or akey != cache[0] or now - cache[1] > 2.0:
            hist = self.archive.query(tag, self.symbol, t0_ms, hi)
            self._ahist_cache[tag] = (akey, now, hist)
        else:
            hist = cache[2]
        seen = {t.trade_id for t in live if t.trade_id}
        return [t for t in hist if t.trade_id not in seen] + live

    def _visible_trades(self, t0: float, t1: float) -> list:
        """Trades de la fenetre visible pour le flux AFFICHE (mono-exchange)."""
        return self._feed_trades(self.exchange, self.inst_type, t0, t1)

    @staticmethod
    def _matched_basis_buckets(bts: list, bpx: list, feed_trades: list) -> dict:
        """Basis PAR bucket de BASIS_BUCKET_MS : chaque trade du flux est apparie en temps
        (<= BASIS_MATCH_MS) au trade Bitget le plus proche ; l'ecart (prix_bitget - prix_feed)
        est range dans le bucket du trade, puis on prend la MEDIANE par bucket. Offset a
        AJOUTER au prix du feed pour le recaler sur Bitget. L'appariement annule le timing
        -> vrai offset de prix ; le decoupage par bucket le fige dans le temps. {} si rien."""
        if not bts or not feed_trades:
            return {}
        by_bucket: dict[int, list] = {}
        n = len(bts)
        for t in feed_trades:
            i = bisect.bisect_left(bts, t.ts)
            j = i if i < n else i - 1
            if i > 0 and (i >= n or abs(bts[i - 1] - t.ts) < abs(bts[j] - t.ts)):
                j = i - 1
            if 0 <= j < n and abs(bts[j] - t.ts) <= BASIS_MATCH_MS:
                by_bucket.setdefault(t.ts // BASIS_BUCKET_MS, []).append(bpx[j] - t.price)
        return {b: float(np.median(d)) for b, d in by_bucket.items()}

    def _update_spreads(self) -> None:
        """Met a jour la carte de basis FIGE par bucket 60 s (self._basis_b), ~1 Hz. Un
        bucket n'est calcule QU'UNE FOIS puis GELE ; seul le bucket COURANT est rafraichi.

        DECOUPLAGE ancre / reference (2026-06-27) : l'axe affiche reste BITGET (reference),
        mais le basis est estime contre le flux le PLUS LIQUIDE (ANCRE = Binance par defaut).
        Mesure : Bitget ~17 trades/30s -> appariement faible (KuCoin 11 % vs Bitget, 89 % vs
        Binance) = recalage bruite. On calcule donc, contre l'ancre dense :
        offset(F) = basis(F<->ancre) - basis(Bitget<->ancre). F=Bitget -> 0 (l'axe ne bouge
        pas). Backfill d'une large fenetre au 1er passage / apres un toggle. cf. docs/hybride.md."""
        if not self.hybrid_perimeter:
            return
        now = time.monotonic()
        if (now - self._basis_t) < 1.0:              # throttle a ~1 Hz
            return
        self._basis_t = now
        ref_key = self.hybrid_perimeter[0][1]
        anchor_key = self._anchor_key()
        cur = int(time.time() * 1000) // BASIS_BUCKET_MS
        span_s = BASIS_BACKFILL_S if not self._basis_filled else 130.0
        wstart = int(time.time() * 1000) - int(span_s * 1000)
        anchor_tr = self.hub.trades_since(anchor_key, self.symbol, wstart)
        ats = [t.ts for t in anchor_tr]
        apx = [t.price for t in anchor_tr]
        # o_ref(bucket) = basis Bitget<->ancre (apparie contre l'ancre dense), gele.
        ref_tr = self.hub.trades_since(ref_key, self.symbol, wstart)
        for b, v in self._matched_basis_buckets(ats, apx, ref_tr).items():
            if b >= cur or b not in self._oref_b:
                self._oref_b[b] = v
        # offset de chaque flux non-ref = o_F - o_ref (les deux contre l'ancre).
        for label, key, _tag in self.hybrid_perimeter[1:]:
            if label not in self.hybrid_enabled:
                continue
            feed_tr = self.hub.trades_since(key, self.symbol, wstart)
            store = self._basis_b.setdefault(key, {})
            for b, val in self._matched_basis_buckets(ats, apx, feed_tr).items():
                if b >= cur or b not in store:       # gele le passe, rafraichit le courant
                    store[b] = val - self._oref_at(b)
        self._basis_filled = True

    def _anchor_key(self) -> str:
        """Flux d'ANCRE pour estimer le basis (le plus liquide -> appariement fiable).
        Binance par defaut (mesure : le plus trade) ; sinon le flux ACTIF le plus trade
        recemment ; sinon la reference. L'axe affiche reste Bitget quoi qu'il arrive."""
        enabled = [(l, k) for l, k, _ in self.hybrid_perimeter if l in self.hybrid_enabled]
        for l, k in enabled:
            if l == "Binance":
                return k
        if enabled:
            ws = int(time.time() * 1000) - 5000
            return max(enabled,
                       key=lambda lk: len(self.hub.trades_since(lk[1], self.symbol, ws)))[1]
        return self.hybrid_perimeter[0][1]

    def _oref_at(self, b: int) -> float:
        """Basis Bitget<->ancre du bucket b (report du dernier connu si manquant : Bitget
        est epars -> ses buckets ont des trous, combles par carry-forward)."""
        v = self._oref_b.get(b)
        if v is not None:
            return v
        if not self._oref_b:
            return 0.0
        ks = sorted(self._oref_b)
        idx = bisect.bisect_right(ks, b) - 1
        return self._oref_b[ks[idx] if idx >= 0 else ks[0]]

    def _shift_trades(self, key: str, trades: list) -> list:
        """Recale les prix d'un flux sur l'axe Bitget. Le recalage de CHAQUE trade est
        calcule UNE SEULE FOIS (a sa premiere vue) puis MIS EN CACHE par trade_id -> un
        trade deja affiche ne bouge JAMAIS, meme quand le basis du bucket COURANT se
        rafraichit en se formant. Copie (replace) -> le buffer du hub n'est jamais mute."""
        store = self._basis_b.get(key)
        cache = self._shift_cache.setdefault(key, {})
        bkeys = bvals = None
        if store:
            items = sorted(store.items())
            bkeys = [b for b, _ in items]
            bvals = [v for _, v in items]
        out = []
        for t in trades:
            st = cache.get(t.trade_id)
            if st is not None:                       # deja recale -> fige (ne rebouge pas)
                out.append(st)
                continue
            if not store:                            # pas encore de basis -> ne pas figer
                out.append(t)
                continue
            b = t.ts // BASIS_BUCKET_MS
            v = store.get(b)
            if v is None:                            # bucket non couvert -> report du voisin
                idx = bisect.bisect_right(bkeys, b) - 1
                v = bvals[idx] if idx >= 0 else bvals[0]
            st = replace(t, price=t.price + v) if v else t
            cache[t.trade_id] = st                    # gele ce trade pour toujours
            out.append(st)
        if len(cache) > 200_000:                      # borne : ne garder que les trades courants
            cur_ids = {t.trade_id for t in trades}
            self._shift_cache[key] = {i: s for i, s in cache.items() if i in cur_ids}
        return out

    def _hybrid_live_entries(self, t0: float, t1: float):
        """(entries, base_trades) pour le footprint live hybride. entries =
        [(trades_recales, with_vol, basis_token)] avec la REFERENCE (Bitget) TOUJOURS en
        premier (corps OHLC + bid/ask + axe, meme si decochee). Les prix des flux non-ref
        sont DEJA recales par bucket fige (_shift_trades). `with_vol` dit si le volume du
        flux est ajoute ; `basis_token` (basis du bucket courant) sert a invalider le cache
        du footprint quand le basis du bucket en formation bouge."""
        self._update_spreads()
        base_label, base_key, base_tag = self.hybrid_perimeter[0]
        base_trades = self._feed_trades(base_key, base_tag, t0, t1)
        entries = [(base_trades, base_label in self.hybrid_enabled, 0.0)]
        cur = int(time.time() * 1000) // BASIS_BUCKET_MS
        for label, key, tag in self.hybrid_perimeter[1:]:
            if label not in self.hybrid_enabled:
                continue
            tr = self._feed_trades(key, tag, t0, t1)
            token = self._basis_b.get(key, {}).get(cur, 0.0)
            entries.append((self._shift_trades(key, tr), True, token))
        return entries, base_trades

    def _hybrid_basis_token(self, t0: float, t1: float):
        """Token IMMUABLE (hashable) du basis trade-matché FIGE du live, par flux non-ref,
        agrege a la resolution d'affichage et borne aux buckets FROZEN de la fenetre :
        ((tag, ((cb, offset), ...)), ...). offset = Bitget-feed a AJOUTER au feed (meme
        convention que _shift_trades). Sert au chemin HISTORIQUE (aggregate_..._hybrid) ->
        meme recalage que le live a la couture AGG_SPAN_S (revue #6). Seuls les buckets
        figes y entrent -> token STABLE pour une fenetre donnee (cache du reader OK) ; les
        buckets non couverts (historique profond) retombent sur le basis des mids cote SQL."""
        factor = max(1, int(round(self.res_s / 60.0)))
        cur = int(time.time() * 1000) // BASIS_BUCKET_MS
        b0 = int(t0 * 1000) // BASIS_BUCKET_MS
        b1 = int(t1 * 1000) // BASIS_BUCKET_MS
        out = []
        for label, key, tag in self.hybrid_perimeter[1:]:
            if label not in self.hybrid_enabled:
                continue
            store = self._basis_b.get(key)
            if not store:
                continue
            by_cb: dict[int, list] = {}
            for b, off in store.items():
                if b >= cur or b < b0 or b > b1:     # seulement les buckets FIGES de la fenetre
                    continue
                by_cb.setdefault(b // factor, []).append(off)
            items = tuple((cb, round(float(np.median(v)), 4)) for cb, v in sorted(by_cb.items()))
            if items:
                out.append((tag, items))
        return tuple(out)

    def _draw_trades(self, t0: float, t1: float) -> None:
        # en mode LIBRE, si la fenetre n'a pas bouge, on ne re-interroge pas
        # l'archive ni ne reconstruit le scatter a chaque frame (perf). On
        # re-verifie quand meme periodiquement (nouvelles donnees de backfill).
        if not self.follow:
            key = (round(t0, 1), round(t1, 1), self.symbol, self.min_size, self.auto_filter)
            now = time.monotonic()
            if key == getattr(self, "_tk", None) and now - getattr(self, "_tt", 0) < 1.5:
                return
            self._tk, self._tt = key, now

        trades = self._visible_trades(t0, t1)
        self.last_trades = trades   # source unique pour le footprint (NON filtree)
        # Y suit l'etendue de prix de la fenetre (footprint) : le scatter, lui, est
        # desormais borne a la derniere heure -> il ne doit plus piloter l'axe Y.
        if trades:
            pr = np.fromiter((t.price for t in trades), np.float64, len(trades))
            self._vis_price = (float(pr.min()), float(pr.max()))
        self._draw_scatter_lasthour(t0, t1)

    def _draw_flow_agg(self, t0: float, t1: float, yr) -> None:
        """ZOOM ARRIERE : footprint AGREGE en SQL (FootprintReader, hors thread Qt)
        -> cout independant du nombre de trades. Les scatters sont traces a part
        (derniere heure seulement) ; ici on ne calcule plus que le footprint."""
        # tick d'affichage borne au tick de base du rollup (le rollup est stocke a
        # cette granularite -> un tick plus fin afficherait des lignes vides).
        tick = self.fp._tick(yr[1] - yr[0])
        if self.archive is not None:
            tick = max(tick, self.archive.base_tick(self.symbol))
        # HYBRIDE : market = (tag de reference, tags des flux non-reference ACTIFS) ->
        # le FootprintReader somme les rollups recales (spread par bucket). Sinon = un tag.
        if self.hybrid:
            self._update_spreads()       # garde le spread frais pour le scatter
            nonbase = tuple(tag for (label, _k, tag) in self.hybrid_perimeter[1:]
                            if label in self.hybrid_enabled)
            base_vol = self.hybrid_perimeter[0][0] in self.hybrid_enabled
            # basis trade-matché FIGE du live -> le chemin historique l'utilise a la couture
            # (sinon discontinuite mids vs trade-matché a AGG_SPAN_S, revue #6).
            basis_tok = self._hybrid_basis_token(t0, t1)
            market = (self.inst_type, nonbase, base_vol, basis_tok)
        else:
            market = self.inst_type
        fkey = self.footprint_reader.key(market, self.symbol,
                                         int(t0 * 1000), int(t1 * 1000), self.res_s, tick)
        # DEBOUNCE : pendant un geste de molette la fenetre change a chaque frame ;
        # on n'enclenche le calcul agrege (couteux) qu'une fois qu'elle s'est STABILISEE
        # (>= AGG_DEBOUNCE_S). Tant qu'elle bouge, on ne demande rien -> l'ancien
        # footprint reste a l'ecran et aucun agregat jetable n'est lance.
        now = time.monotonic()
        if fkey != self._agg_key:
            self._agg_key, self._agg_since = fkey, now
        if now - self._agg_since >= AGG_DEBOUNCE_S:
            self.footprint_reader.request(fkey)
        candles = self.footprint_reader.get(fkey)
        self._agg_bbo = self.footprint_reader.get_bbo(fkey)   # lignes bid/ask fines (ou None)
        self._draw_scatter_lasthour(t0, t1)
        if candles:
            self._agg_candles = candles    # repli bid/ask (pas 60 s) + footprint
            if self.fp.item.isVisible():
                self.fp.item.set_data(candles, tick)
            # Y englobe la plage des BOUGIES (le scatter est borne a la derniere heure)
            self._vis_price = (min(c.l for c in candles), max(c.h for c in candles))

    def _draw_scatter_lasthour(self, t0: float, t1: float) -> None:
        """Scatters UNIQUEMENT sur la derniere heure reelle (relatif au PRESENT), a
        toute echelle de zoom. Au-dela (navigation historique), aucun scatter :
        footprint + lignes best bid/ask seulement."""
        now = time.time()
        s0 = max(t0, now - LIVE_SCATTER_S)
        s1 = min(t1, now)
        if s1 <= s0:                       # fenetre visible hors de la derniere heure
            self.buys.setData(x=[], y=[], size=[])
            self.sells.setData(x=[], y=[], size=[])
            return
        if self.hybrid:
            trades = self._hybrid_lasthour_trades(s0)
        else:
            trades = self.hub.trades_since(self.exchange, self.symbol, int(s0 * 1000))
        self._render_scatter(trades, s0, s1)

    def _hybrid_lasthour_trades(self, s0: float) -> list:
        """Trades (derniere heure) de tous les flux actifs, recales sur Bitget par le
        basis FIGE de chaque bucket (copie decalee, sans muter le buffer du hub)."""
        s0_ms = int(s0 * 1000)
        out: list = []
        for i, (label, key, _tag) in enumerate(self.hybrid_perimeter):
            if label not in self.hybrid_enabled:
                continue
            tr = self.hub.trades_since(key, self.symbol, s0_ms)
            out.extend(tr if i == 0 else self._shift_trades(key, tr))
        out.sort(key=lambda t: t.ts)
        return out

    def _render_scatter(self, trades: list, t0: float, t1: float) -> None:
        """Filtre (numpy) + dessine le scatter a partir d'une liste de trades."""
        ref = REF_VOL.get(self.symbol, 1.0)
        n = len(trades)
        if n == 0:
            self.buys.setData(x=[], y=[], size=[])
            self.sells.setData(x=[], y=[], size=[])
            return
        # --- FILTRE VECTORISE (numpy) : O(n) sans boucle Python, gros volumes ---
        # arrays une seule fois ; on travaille ensuite par indices/masques.
        ts = np.fromiter((t.ts for t in trades), np.float64, n) / 1000.0
        price = np.fromiter((t.price for t in trades), np.float64, n)
        size = np.fromiter((t.size for t in trades), np.float64, n)
        # NB : _vis_price (etendue Y) est desormais pilote par le footprint/les bougies
        # (le scatter est borne a la derniere heure -> il ne represente plus la plage).
        is_buy = np.fromiter((t.side == "buy" for t in trades), bool, n)
        # 1) filtre MANUEL (Min size) + borne droite, vectorise
        idx = np.nonzero((size >= self.min_size) & (ts <= t1))[0]
        # 2) filtre AUTO : cible de points ADAPTEE A LA PLAGE (plus la plage est
        #    grande, moins on affiche de points -> on ne garde que les plus gros),
        #    appliquee via un SEUIL DE TAILLE STABLE (hysteresis) : un point reste
        #    affiche tant que sa taille >= seuil, et le seuil ne bouge que s'il
        #    derive nettement (>25%). Sinon le top-N recalcule a chaque frame fait
        #    clignoter (apparition/disparition) les points en bord de selection.
        if self.auto_filter:
            span = max(t1 - t0, 1.0)
            # cible de points qui DECROIT avec la plage : plus on dezoom, moins on
            # garde de scatters (uniquement les plus gros). Exposant AUTO_DECAY > 0.5
            # -> se resserre plus vite que l'ancienne racine carree (moins d'encombrement
            # sur les grandes periodes).
            cap = int(np.clip(MAX_POINTS * (DEFAULT_SPAN / span) ** AUTO_DECAY,
                              MIN_POINTS, MAX_POINTS))
            if idx.size > cap:
                target = float(np.partition(size[idx], -cap)[-cap])
                prev = self._auto_thresh
                if prev <= 0 or not (0.8 <= target / max(prev, 1e-9) <= 1.25):
                    self._auto_thresh = target       # derive nette -> on reajuste
                idx = idx[size[idx] >= max(self.min_size, self._auto_thresh)]
        if idx.size > HARD_CAP:                      # garde-fou de rendu (perf)
            idx = idx[np.argpartition(size[idx], -HARD_CAP)[-HARD_CAP:]]
        # ORDRE DE DESSIN STABLE : trie par volume CROISSANT -> les gros points sont
        # dessines en DERNIER (donc au-dessus), et l'empilement des points qui se
        # chevauchent ne change plus d'une frame a l'autre. Sans ce tri, l'ordre
        # arbitraire d'argpartition permute avant/arriere a chaque frame -> les
        # scatters "clignotent" en passant derriere/devant.
        idx = idx[np.argsort(size[idx], kind="stable")]
        # 3) tailles de points vectorisees (avec facteur de taille reglable)
        dot = (DOT_MIN + DOT_SPAN * np.sqrt(np.maximum(size[idx], 0.0) / ref)) * self.dot_scale
        np.minimum(dot, DOT_MAX * 1.6, out=dot)
        b = is_buy[idx]
        self.buys.setData(x=ts[idx][b], y=price[idx][b], size=dot[b])
        self.sells.setData(x=ts[idx][~b], y=price[idx][~b], size=dot[~b])

    def _draw_dom(self, book, grid) -> None:
        # Orderbook agrege sur la MEME grille que la heatmap -> chaque barre du
        # carnet correspond exactement a une rangee de la heatmap.
        if grid is None:
            return
        tick, row0, nb, y0, _ = grid
        centers = y0 + (np.arange(nb) + 0.5) * tick
        if self.hybrid:
            # carnet HYBRIDE = UN SEUL total cumule (somme des flux recales), couleurs
            # bid/ask classiques (pas de ventilation par exchange).
            bid_v, ask_v = self._hybrid_book_binned(tick, row0, nb)
        else:
            if not book.bids or not book.asks:
                return
            bid_v = self._bin(np.array([p for p, _ in book.bids]),
                              np.array([s for _, s in book.bids]), tick, row0, nb)
            ask_v = self._bin(np.array([p for p, _ in book.asks]),
                              np.array([s for _, s in book.asks]), tick, row0, nb)
        bm, am = bid_v > 0, ask_v > 0
        self.dom_bids.setOpts(x0=0, y=centers[bm], height=tick * 0.9, width=bid_v[bm])
        self.dom_asks.setOpts(x0=0, y=centers[am], height=tick * 0.9, width=ask_v[am])
        mx = float(max(bid_v.max(), ask_v.max(), 1e-9))
        self.dom.setXRange(0, mx * 1.1, padding=0)

    def _hybrid_book_binned(self, tick: float, row0: int, nb: int):
        """Carnet HYBRIDE = SOMME CUMULEE des carnets des flux actifs, chacun recale sur
        l'axe Bitget (basis du bucket courant) et binne sur la grille commune. Renvoie
        (bid_v, ask_v) sommes par niveau -> un seul orderbook cumule (pas de provenance).

        ANTI-CROISEMENT : les meilleurs bid/ask different d'une venue a l'autre, donc apres
        recalage l'ask d'une venue peut tomber au niveau du bid d'une autre (et inversement)
        -> on verrait des bids cote ask et des asks cote bid. On impose une FRONTIERE unique =
        le mid de REFERENCE (Bitget) : chaque flux ne contribue ses bids que SOUS le mid et
        ses asks qu'AU-DESSUS. Les niveaux qui croiseraient (locked/arbitrage) sont ecartes
        de l'affichage (ils relevent de la Phase 3 arbitrage)."""
        cur = int(time.time() * 1000) // BASIS_BUCKET_MS
        bid_v = np.zeros(nb, dtype=np.float32)
        ask_v = np.zeros(nb, dtype=np.float32)
        # mid de reference (Bitget = 1er du perimetre) -> frontiere bid/ask. Bitget reste la
        # reference de prix meme decoche, donc on lit son carnet ici independamment du toggle.
        ref_book = self.hub.book(self.hybrid_perimeter[0][1], self.symbol)
        mid = ref_book.mid if (ref_book.bids and ref_book.asks) else None
        for i, (label, key, _tag) in enumerate(self.hybrid_perimeter):
            if label not in self.hybrid_enabled:
                continue
            b = self.hub.book(key, self.symbol)
            if not b.bids or not b.asks:
                continue
            s = 0.0 if i == 0 else float(self._basis_b.get(key, {}).get(cur, 0.0))
            bp = np.fromiter((p for p, _ in b.bids), np.float64, len(b.bids)) + s
            bs = np.fromiter((sz for _, sz in b.bids), np.float64, len(b.bids))
            ap = np.fromiter((p for p, _ in b.asks), np.float64, len(b.asks)) + s
            az = np.fromiter((sz for _, sz in b.asks), np.float64, len(b.asks))
            if mid is not None:                          # chaque cote reste du BON cote du mid
                mb = bp < mid
                bp, bs = bp[mb], bs[mb]
                ma = ap > mid
                ap, az = ap[ma], az[ma]
            bid_v += self._bin(bp, bs, tick, row0, nb)
            ask_v += self._bin(ap, az, tick, row0, nb)
        return bid_v, ask_v

    def _run_arbitrage(self) -> None:
        """Dislocation de carnet live (Phase 3) : sur les carnets BRUTS (non recales) de
        toutes les venues du perimetre, on cherche les croisements nets de frais taker ET
        de notional suffisant (find_arbs ; arb_min_notional filtre les tailles derisoires).
        ⚠ signal de dislocation, PAS un arbitrage executable sans risque (cf. arbitrage.py).
        Resultat dans self._arbs (barre d'etat) ; chaque cas est journalise (arb.db),
        throttle a 1/s par paire pour ne pas inonder."""
        books = {}
        for label, key, _tag in self.hybrid_perimeter:
            b = self.hub.book(key, self.symbol)
            if b is not None and b.bids and b.asks:
                books[label] = b
        fees = {label: self.fees.get(label, {}).get(self.hybrid_market, 0.0)
                for label, _k, _t in self.hybrid_perimeter}
        ts = int(time.time() * 1000)
        self._arbs = find_arbs(books, fees, self.arb_threshold, ts, self.arb_min_notional)
        if not self._arbs:
            return
        now = time.monotonic()
        for a in self._arbs:
            k = (a.sell_ex, a.buy_ex)
            if now - self._arb_log_t.get(k, 0.0) >= 1.0:   # throttle 1/s par paire
                self._arb_log_t[k] = now
                self._arb_pts.append((ts / 1000.0, a.mid))  # marqueur graphe
                if self.arb_archive is not None:
                    self.arb_archive.insert(ts, self.symbol, self.hybrid_market,
                                            a.sell_ex, a.buy_ex, a.edge, a.net_bps, a.net_usd)

    def _draw_arb_markers(self, t0: float, t1: float) -> None:
        """Marqueurs d'arbitrage (etoiles) au prix/temps des opportunites journalisees,
        bornes a la DERNIERE HEURE et a la fenetre visible (comme les scatters)."""
        if not self.hybrid or not self._arb_pts:
            self.arb_marks.setData(x=[], y=[])
            return
        lo = max(t0, time.time() - LIVE_SCATTER_S)
        xs = [t for t, _ in self._arb_pts if lo <= t <= t1]
        ys = [p for t, p in self._arb_pts if lo <= t <= t1]
        self.arb_marks.setData(x=xs, y=ys)

    def _update_title(self, book) -> None:
        # info utile (pas generique) : paire + dernier prix dans la devise choisie
        mid = book.mid
        if mid:
            px = mid * self.price_mult()
            cur = "$CA" if self.currency == "CAD" else "$US"
            txt = f"{self.symbol}   {px:,.2f} {cur}"
        else:
            txt = self.symbol
        if self.hybrid and self._arbs:                # meilleure DISLOCATION de carnet
            a = self._arbs[0]
            usd = a.net_usd * self.price_mult()
            cur = "$CA" if self.currency == "CAD" else "$US"
            notional = a.notional * self.price_mult()
            txt += (f"      ⚡ disloc {a.sell_ex}→{a.buy_ex}  +{a.net_bps:.1f} bps "
                    f"({usd:,.2f} {cur}/u · {a.qty:.3f}≈{notional:,.0f} {cur})")
        self.status.setText(txt)
