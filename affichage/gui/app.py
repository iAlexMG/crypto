"""Fenetre principale du dashboard orderflow (PySide6 + pyqtgraph)."""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDateTime, QTimer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDateTimeEdit,
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget,
)

from backend.archive import TradeArchive
from backend.book_archive import BookArchive
from backend.arb_archive import ArbArchive
from backend.models import SYMBOLS
from .backfill import BackfillManager
from .book_reader import BookReader
from .book_recorder import BookRecorder
from .collector import HistoryCollector
from .footprint_reader import FootprintReader
from .rollup_maintainer import RollupMaintainer
from .controls import LayersPanel
from .arb_settings import ArbSettingsDialog
from .currency import FxRate
from .history import HistoryStore
from .logsetup import setup_logging
from .orderflow_view import OrderflowView
from .service import (
    ConnectorService, FEEDS, EXCHANGES, MARKETS, DEFAULT_EXCHANGE,
    DEFAULT_MARKET, feed_for, markets_for, fetch_for, fetch_before_for,
    fetch_range_for, HYBRID_LABEL, hybrid_feeds, hybrid_base,
)

DATA_DIR = "data"  # dossier dedie des bases SQLite (trades.db, books.db + WAL)
REFRESH_MS = 100   # ~10 images/s (rendu)
SAMPLE_MS = 200    # cadence d'appel du sampler (throttle interne a SAMPLE_MS de history)
HEALTH_MS = 15000  # log de sante par flux toutes les 15s (age dernier trade / book)
TZ = ZoneInfo("America/Toronto")   # heure du Quebec (saisie de date)

log = logging.getLogger("gui")

# resolutions de bougies (label -> secondes). La plage initiale affiche ~TARGET_CANDLES
# bougies ; la resolution est ensuite FIXE (le zoom ne la change plus).
RESOLUTIONS = [("1m", 60), ("5m", 300), ("15m", 900), ("30m", 1800),
               ("1h", 3600), ("4h", 14400), ("1j", 86400)]
TARGET_CANDLES = 22

_BTN_CSS = (
    "QPushButton{padding:6px 16px;font-weight:600;color:#8b949e;"
    "background:#1c222b;border:1px solid #2b3340;}"
    "QPushButton:checked{background:#58a6ff;color:#06101f;}"
)
_LBL_CSS = "color:#8b949e;font-weight:600;"


class MainWindow(QMainWindow):
    def __init__(self, service: ConnectorService) -> None:
        super().__init__()
        self.service = service
        self.symbol = SYMBOLS[0]
        self.exchange_label = DEFAULT_EXCHANGE
        self.market = DEFAULT_MARKET
        self.feed = feed_for(self.exchange_label, self.market)
        self.exchange = self.feed.key          # cle de flux hub du flux affiche
        # un historique (heatmap) PAR FLUX (exchange × marche), sur sa cle propre.
        # TOUS les flux sont captes en continu -> aucun gel quand on bascule.
        self._stores = {f.key: HistoryStore(f.key, list(SYMBOLS)) for f in FEEDS}
        self.store = self._stores[self.feed.key]
        self.fx = FxRate()
        # toutes les bases SQLite dans un dossier dedie (cree au besoin)
        os.makedirs(DATA_DIR, exist_ok=True)
        # archive disque + backfill historique des trades (TOUS les flux archives)
        self.archive = TradeArchive(os.path.join(DATA_DIR, "trades.db"))
        # CAPTURE le ts du dernier trade archive AVANT que quoi que ce soit
        # n'ecrive cette session (backfill/live polluent `newest()` en quelques
        # secondes). C'est la FIN de la session precedente = debut du TROU a combler.
        startup_newest = {(f.market_tag, s): self.archive.newest(f.market_tag, s)
                          for f in FEEDS for s in SYMBOLS}
        self.backfill = BackfillManager(
            self.archive, service.hub, list(SYMBOLS),
            feeds=[(f.key, f.market_tag, fetch_for(f)) for f in FEEDS],
            active_key=self.feed.key, res_s=float(RESOLUTIONS[0][1]))
        self.backfill.start()
        # collecteur d'historique : (A) comble le TROU present, (B) remonte le passe.
        # BITGET (futures) = base d'ancrage de tout l'historique (plancher commun).
        _base = feed_for(DEFAULT_EXCHANGE, DEFAULT_MARKET)
        self.collector = HistoryCollector(
            self.archive, service.hub,
            feeds=[(f.key, f.exchange, f.market_tag,
                    fetch_before_for(f), fetch_range_for(f)) for f in FEEDS],
            symbols=list(SYMBOLS), startup_newest=startup_newest,
            base_exchange=_base.exchange, base_tag=_base.market_tag)
        self.collector.set_active(self.feed.key, self.symbol)
        self.collector.start()
        # historique du CARNET (heatmap) : base SEPAREE, persistee en live (le
        # carnet n'a pas d'historique REST -> on capte les snapshots au fil du temps).
        self.book_archive = BookArchive(os.path.join(DATA_DIR, "books.db"))
        self.book_recorder = BookRecorder(
            self.book_archive, service.hub,
            feeds=[(f.key, f.market_tag) for f in FEEDS], symbols=list(SYMBOLS))
        self.book_recorder.start()
        # lecteur ASYNCHRONE de books.db (heatmap historique au-dela des ~2 h
        # gardees en memoire) : lecture bornee, hors thread Qt -> pas de gel.
        self.book_reader = BookReader(self.book_archive)
        self.book_reader.start()
        # lecteur ASYNCHRONE du footprint agrege (zoom arriere) : agregation SQL
        # bornee + gros trades, hors thread Qt -> plus de gel sur grande plage.
        self.footprint_reader = FootprintReader(self.archive)
        self.footprint_reader.start()
        # rollup pre-agrege du footprint (lecture zoom arriere quasi instantanee) +
        # retention (purge des trades bruts > 7 j deja agreges, et des snapshots de
        # carnet) -> borne la taille disque. Tourne dans son propre thread.
        # journal des opportunites d'arbitrage (Phase 3, base separee data/arb.db).
        self.arb_archive = ArbArchive(os.path.join(DATA_DIR, "arb.db"))
        self.rollup = RollupMaintainer(self.archive, self.book_archive,
                                       arb_archive=self.arb_archive)
        self.rollup.start()
        self.setWindowTitle("Crypto Orderflow — multi-exchanges")
        self.resize(1320, 820)

        # graphe UNIQUE : footprint (avant-plan) + orderflow superposes. Cree avant
        # la barre car les boutons de couches s'y connectent.
        self.view = OrderflowView(self.store, service.hub, self.exchange,
                                  self.symbol, fx=self.fx,
                                  archive=self.archive, backfill=self.backfill,
                                  book_reader=self.book_reader,
                                  footprint_reader=self.footprint_reader,
                                  arb_archive=self.arb_archive)
        self.view.inst_type = self.feed.market_tag
        self.view.set_resolution(float(RESOLUTIONS[0][1]))

        bar = QHBoxLayout()
        # (l'exchange affiche est indique par les boutons de selection -> pas de label)
        rlbl = QLabel("Résolution")
        rlbl.setStyleSheet(_LBL_CSS)
        bar.addWidget(rlbl)
        self._res = QComboBox()
        for lab, sec in RESOLUTIONS:
            self._res.addItem(lab, sec)
        self._res.setStyleSheet(
            "QComboBox{padding:4px 8px;color:#c9d1d9;background:#1c222b;"
            "border:1px solid #2b3340;}"
            "QComboBox QAbstractItemView{background:#1c222b;color:#c9d1d9;"
            "selection-background-color:#58a6ff;selection-color:#06101f;}")
        self._res.currentIndexChanged.connect(self._change_resolution)
        bar.addWidget(self._res)

        bar.addStretch(1)

        # saut a une date precise (evite de faire defiler a la souris sur 90 j)
        dlbl = QLabel("Aller au")
        dlbl.setStyleSheet(_LBL_CSS)
        bar.addWidget(dlbl)
        self._date = QDateTimeEdit(QDateTime.currentDateTime())
        self._date.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._date.setCalendarPopup(True)
        self._date.setStyleSheet(
            "QDateTimeEdit{padding:4px 6px;color:#c9d1d9;background:#1c222b;"
            "border:1px solid #2b3340;}")
        bar.addWidget(self._date)
        gobtn = QPushButton("Aller")
        gobtn.setStyleSheet(_BTN_CSS)
        gobtn.clicked.connect(self._goto_date)
        bar.addWidget(gobtn)
        bar.addSpacing(12)

        # --- Source des donnees : Exchange (les sites) + Marche (Futures/Spot),
        #     regroupes en menus deroulants (plus de boutons epars). ---
        _COMBO_CSS = (
            "QComboBox{padding:4px 8px;color:#c9d1d9;background:#1c222b;"
            "border:1px solid #2b3340;}"
            "QComboBox QAbstractItemView{background:#1c222b;color:#c9d1d9;"
            "selection-background-color:#58a6ff;selection-color:#06101f;}")
        self._exch = QComboBox()
        self._exch.addItems(EXCHANGES)
        self._exch.addItem(HYBRID_LABEL)   # entree virtuelle : agregation recalee sur Bitget
        self._exch.setCurrentText(DEFAULT_EXCHANGE)
        self._exch.setStyleSheet(_COMBO_CSS)
        self._exch.currentTextChanged.connect(self._switch_exchange)
        bar.addWidget(self._exch)

        self._mkt = QComboBox()
        for mk in MARKETS:
            self._mkt.addItem(mk.capitalize(), mk)
        self._mkt.setCurrentText(DEFAULT_MARKET.capitalize())
        self._mkt.setStyleSheet(_COMBO_CSS)
        self._mkt.currentIndexChanged.connect(
            lambda _=0: self._switch_market(self._mkt.currentData()))
        bar.addWidget(self._mkt)
        bar.addSpacing(10)
        # (Min size / Auto / taille des points -> deplaces dans le popup Trades de
        #  « Affichage », avec les autres reglages du type Trades.)

        # --- Affichage : configuration groupee des couches (popup) :
        #     Footprint / Heatmap / Trades (visible + opacite + avant/arriere)
        #     + Carnet. Remplace les boutons epars de l'entete. ---
        bar.addSpacing(10)
        self._layers_panel = LayersPanel(self.view)
        self._btn_layers = QPushButton("Affichage ⚙")
        self._btn_layers.setStyleSheet(_BTN_CSS)
        self._btn_layers.clicked.connect(
            lambda: self._layers_panel.popup_under(self._btn_layers))
        bar.addWidget(self._btn_layers)

        # frais taker + seuil + notional mini (table editable) -> s'applique en mode Hybride.
        self._btn_arb = QPushButton("Dislocation ⚙")
        self._btn_arb.setStyleSheet(_BTN_CSS)
        self._btn_arb.clicked.connect(self._open_arb_settings)
        bar.addWidget(self._btn_arb)

        # mode d'affichage : un seul bouton Live (bascule). Glisser = quitte Live.
        bar.addSpacing(10)
        self._btn_live = QPushButton("● Live")
        self._btn_live.setCheckable(True)
        self._btn_live.setChecked(True)
        self._btn_live.setStyleSheet(_BTN_CSS)
        self._btn_live.clicked.connect(self._go_live)
        bar.addWidget(self._btn_live)

        self._group = QButtonGroup(self)
        for sym in SYMBOLS:
            btn = QPushButton(sym.replace("USDT", "/USDT"))
            btn.setCheckable(True)
            btn.setChecked(sym == self.symbol)
            btn.clicked.connect(lambda _=False, s=sym: self._switch(s))
            btn.setStyleSheet(_BTN_CSS)
            self._group.addButton(btn)
            bar.addWidget(btn)

        quit_btn = QPushButton("✕ Quitter")
        quit_btn.setStyleSheet(
            "QPushButton{padding:6px 14px;font-weight:600;color:#f0b0b0;"
            "background:#2a1c1f;border:1px solid #5a2b30;}")
        quit_btn.clicked.connect(self.close)
        bar.addSpacing(10)
        bar.addWidget(quit_btn)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.addLayout(bar)
        root.addWidget(self.view, 1)   # graphe unique : footprint + orderflow superposes
        self.setCentralWidget(central)
        self.setStyleSheet("background:#0d1117;")

        # echantillonne la heatmap des DEUX marches en continu (pas seulement
        # l'affiche) -> le marche non affiche n'a plus de trou a son retour.
        self._sampler = QTimer(self)
        self._sampler.timeout.connect(self._sample_all)
        self._sampler.start(SAMPLE_MS)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)

        # log de sante : age du dernier trade / du dernier update de book, PAR flux
        # -> repere precisement un canal trade fige (ex. Binance Futures) ou un book
        #    qui ne bouge plus, sans logger aucune donnee de marche.
        self._health = QTimer(self)
        self._health.timeout.connect(self._log_health)
        self._health.start(HEALTH_MS)

    def _sample_all(self) -> None:
        for store in self._stores.values():
            store.sample(self.service.hub)

    def _log_health(self) -> None:
        now = time.time() * 1000.0
        hub = self.service.hub
        for f in FEEDS:
            parts = []
            for s in SYMBOLS:
                tts = hub.last_trade_ts(f.key, s)
                bts = hub.book(f.key, s).ts
                ta = f"{(now - tts) / 1000:.0f}s" if tts else "—"
                ba = f"{(now - bts) / 1000:.0f}s" if bts else "—"
                parts.append(f"{s[:3]} trade={ta} book={ba}")
            log.info("sante %-13s %s", f.key, " | ".join(parts))

    def _go_live(self) -> None:
        self.view.go_live()
        self._btn_live.setChecked(True)

    def _tick(self) -> None:
        self.view.refresh()   # rend l'orderflow ET la couche footprint superposee
        # le bouton Live reflete l'etat reel (un glissement souris quitte le Live)
        if self._btn_live.isChecked() != self.view.follow:
            self._btn_live.setChecked(self.view.follow)

    def _switch(self, symbol: str) -> None:
        self.symbol = symbol
        self.view.set_symbol(symbol)
        self.collector.set_active(self.feed.key, symbol)   # priorite collecteur

    def _change_resolution(self) -> None:
        res = float(self._res.currentData())
        self.view.set_resolution(res)
        self.backfill.set_resolution(res)   # recharge le prefixe de la chandelle ouverte
        # largeur initiale ~ TARGET_CANDLES bougies ; PAS de plafond de plage
        # (zoom out libre -> le filtre Auto s'adapte a la plage).
        self.view.go_live()
        self.view.x_span = max(60.0, min(res * TARGET_CANDLES, self.view.max_span))

    def _goto_date(self) -> None:
        dt = self._date.dateTime().toPython().replace(tzinfo=TZ)
        t_start = dt.timestamp()
        self.view.goto(t_start)
        # plus de backfill a la demande : on AFFICHE ce que le collecteur a deja
        # archive dans trades.db (l'historique se remplit en arriere-plan).

    def _switch_exchange(self, exchange: str) -> None:
        if exchange != self.exchange_label:
            self.exchange_label = exchange
            self._apply_feed()

    def _switch_market(self, market: str) -> None:
        if market != self.market:
            self.market = market
            self._apply_feed()

    def _apply_feed(self) -> None:
        # AUCUN redemarrage : TOUS les flux tournent deja. On change seulement le
        # flux LU (cle hub + historique + tag d'archive). Les autres continuent.
        if self.exchange_label == HYBRID_LABEL:
            self._apply_hybrid()
            return
        # Tous les exchanges n'offrent pas les deux marches (Coinbase = SPOT seul) :
        # si le marche courant n'existe pas pour cet exchange, on bascule sur un
        # marche disponible et on met le menu a jour (au lieu de planter).
        avail = markets_for(self.exchange_label)
        if avail and self.market not in avail:
            self.market = avail[0]
            self._mkt.blockSignals(True)
            self._mkt.setCurrentText(self.market.capitalize())
            self._mkt.blockSignals(False)
        self.feed = feed_for(self.exchange_label, self.market)
        self.exchange = self.feed.key
        self.store = self._stores[self.feed.key]
        self.backfill.set_active(self.feed.key)
        self.collector.set_active(self.feed.key, self.symbol)   # priorite collecteur
        self.view.set_store(self.store)
        self.view.set_market(self.feed.key, self.feed.market_tag)

    def _apply_hybrid(self) -> None:
        """Mode HYBRIDE : agregation recalee sur Bitget. L'archive/backfill/collecteur
        et les couches non encore hybrides (heatmap/DOM/bid-ask) restent ancres sur le
        flux de REFERENCE Bitget ; la vue agrege le footprint des exchanges du perimetre
        ACTIVE (etape 1 : Bitget seul -> identique a Bitget). Voir docs/hybride.md."""
        if self.market not in MARKETS:
            self.market = DEFAULT_MARKET
        base = hybrid_base(self.market)
        self.feed = base                       # reference Bitget (archive/backfill/collecteur)
        self.exchange = base.key
        self.store = self._stores[base.key]
        self.backfill.set_active(base.key)
        self.collector.set_active(base.key, self.symbol)
        perimeter = [(f.exchange, f.key, f.market_tag) for f in hybrid_feeds(self.market)]
        # tous les flux sont deja echantillonnes en memoire (self._stores) -> la vue peut
        # fusionner la heatmap des flux du perimetre (Phase 2b). `self.market` (FUTURES/SPOT)
        # selectionne la table de frais taker pour l'arbitrage (Phase 3).
        self.view.set_hybrid(perimeter, base.key, base.market_tag, self.store,
                             self._stores, self.market)

    def _open_arb_settings(self) -> None:
        """Ouvre le dialogue d'edition des frais taker et du seuil d'arbitrage (modal)."""
        ArbSettingsDialog(self.view, self).exec()

    def _shutdown(self) -> None:
        """Arret propre : on coupe d'abord les timers (sinon refresh() interroge
        une archive deja fermee), puis backfill / archive / connecteurs."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._timer.stop()
        self._sampler.stop()
        self._health.stop()
        self.backfill.stop()
        self.collector.stop()
        self.book_recorder.stop()
        self.book_reader.stop()
        self.footprint_reader.stop()
        self.rollup.stop()
        self.archive.close()
        self.book_archive.close()
        self.arb_archive.close()
        self.service.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._shutdown()
        super().closeEvent(event)


def main() -> None:
    path = setup_logging()
    log.info("=== demarrage dashboard ; log -> %s ===", path)
    service = ConnectorService()
    service.start()

    app = QApplication(sys.argv)
    win = MainWindow(service)
    # plein ecran des le demarrage, sur l'ecran courant ; fenetre normale (avec
    # bordures) -> deplacable vers un autre ecran, le layout s'adapte tout seul.
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
