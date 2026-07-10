"""Dialogue d'edition des frais taker, du seuil et du notional mini (Phase 3b).

Edite la copie MUTABLE portee par la vue (`view.fees`, `view.arb_threshold`,
`view.arb_min_notional`) ; le moteur `find_arbs` les recoit en parametres -> les changements
s'appliquent au prochain rafraichissement. Style aligne sur le theme sombre (cf. gui/controls.py).
"""
from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDoubleSpinBox,
                               QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

_MARKETS = ("FUTURES", "SPOT")

_CSS = (
    "QDialog{background:#11161f;}"
    "QLabel{color:#8b949e;font-size:12px;}"
    "QTableWidget{background:#0d1117;color:#c9d1d9;gridline-color:#2b3340;"
    "border:1px solid #2b3340;font-size:12px;}"
    "QTableWidget::item{padding:4px 8px;}"
    "QTableWidget::item:selected{background:#1f6feb;color:#ffffff;}"
    "QHeaderView::section{background:#1c222b;color:#8b949e;padding:5px 10px;"
    "border:0;border-right:1px solid #2b3340;border-bottom:1px solid #2b3340;"
    "font-weight:600;}"
    "QTableCornerButton::section{background:#1c222b;border:0;}"
    "QDoubleSpinBox{color:#ffffff;background:#1c222b;border:1px solid #2b3340;"
    "padding:3px 6px;min-width:64px;}"
    "QPushButton{padding:6px 18px;font-weight:600;color:#c9d1d9;background:#1c222b;"
    "border:1px solid #2b3340;}"
    "QPushButton:hover{background:#283041;}"
)


class ArbSettingsDialog(QDialog):
    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.setWindowTitle("Dislocation — frais taker, seuil & notional")
        self.setStyleSheet(_CSS)
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        lay.addWidget(QLabel("Frais TAKER par exchange (en bps ; 10 bps = 0,10 %) :"))

        self._exes = list(view.fees.keys())
        self.table = QTableWidget(len(self._exes), len(_MARKETS), self)
        self.table.setVerticalHeaderLabels(self._exes)
        self.table.setHorizontalHeaderLabels(["Futures (bps)", "Spot (bps)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(True)
        for r, ex in enumerate(self._exes):
            for c, mk in enumerate(_MARKETS):
                self.table.setItem(r, c, QTableWidgetItem(f"{view.fees[ex].get(mk, 0.0):g}"))
        # hauteur = en-tete + lignes (pas d'espace vide ni d'ascenseur)
        h = self.table.horizontalHeader().height() + 2
        h += sum(self.table.rowHeight(r) for r in range(len(self._exes)))
        self.table.setFixedHeight(h)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Seuil net (bps) :"))
        self.thr = QDoubleSpinBox(self)
        self.thr.setRange(-100.0, 1000.0)
        self.thr.setDecimals(2)
        self.thr.setSingleStep(0.5)
        self.thr.setValue(float(view.arb_threshold))
        row.addWidget(self.thr)
        row.addSpacing(12)
        row.addWidget(QLabel("Notional mini ($) :"))
        self.notional = QDoubleSpinBox(self)
        self.notional.setRange(0.0, 1_000_000.0)
        self.notional.setDecimals(0)
        self.notional.setSingleStep(500.0)
        self.notional.setValue(float(getattr(view, "arb_min_notional", 0.0)))
        row.addWidget(self.notional)
        row.addStretch(1)
        lay.addLayout(row)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        cancel = QPushButton("Annuler", self)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK", self)
        ok.setStyleSheet("QPushButton{background:#1f6feb;color:#ffffff;border:0;"
                         "padding:6px 18px;font-weight:600;}"
                         "QPushButton:hover{background:#388bfd;}")
        ok.clicked.connect(self._apply)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _apply(self) -> None:
        for r, ex in enumerate(self._exes):
            for c, mk in enumerate(_MARKETS):
                item = self.table.item(r, c)
                if item is None:
                    continue
                try:
                    self.view.fees[ex][mk] = float(item.text().replace(",", ".").strip())
                except ValueError:
                    pass                     # valeur invalide -> on garde l'ancienne
        self.view.arb_threshold = float(self.thr.value())
        self.view.arb_min_notional = float(self.notional.value())
        self.accept()
