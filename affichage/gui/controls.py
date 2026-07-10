"""Configuration de l'AFFICHAGE en popups.

- LayersPanel  : liste compacte des types de donnees (Footprint / Heatmap /
  Trades / Carnet) avec une case Visible et un engrenage par type.
- TypeSettings : popup COMPLET d'un type (visible, opacite, ordre avant/arriere,
  + options specifiques : pour Trades -> taille des points, Min size, Auto).

Tout pilote directement l'OrderflowView.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

# types superposes : cle interne -> libelle (le Carnet/DOM est gere a part)
OVERLAY = {"footprint": "Footprint", "heatmap": "Heatmap", "trades": "Trades"}

_CSS = (
    "QWidget{background:#11161f;color:#c9d1d9;}"
    "QCheckBox{font-weight:600;padding:2px;}"
    "QLabel{color:#8b949e;font-size:11px;}"
    "QPushButton{background:#1c222b;border:1px solid #2b3340;color:#c9d1d9;"
    "padding:2px 8px;}"
    "QPushButton:hover{background:#283041;}"
    "QDoubleSpinBox{padding:2px 6px;color:#fff;background:#1c222b;border:1px solid #2b3340;}"
)


class _Popup(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.Popup)
        self.setStyleSheet(_CSS)

    def show_under(self, widget) -> None:
        self.move(widget.mapToGlobal(widget.rect().bottomLeft()))
        self.show()


class TypeSettings(_Popup):
    """Popup complet de configuration d'un type de donnees."""

    def __init__(self, view, name: str) -> None:
        super().__init__()
        self.view = view
        self.name = name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        title = QLabel(OVERLAY.get(name, "Carnet"))
        f = title.font(); f.setBold(True); f.setPointSize(f.pointSize() + 1)
        title.setFont(f); title.setStyleSheet("color:#c9d1d9;")
        lay.addWidget(title)

        # --- Carnet (DOM) : panneau lateral -> visibilite seule ---
        if name == "dom":
            vis = QCheckBox("Visible")
            vis.setChecked(view.dom.isVisible())
            vis.toggled.connect(view.set_dom_visible)
            lay.addWidget(vis)
            return

        items = view._layers[name]
        # Visible
        vis = QCheckBox("Visible")
        vis.setChecked(items[0].isVisible())
        vis.toggled.connect(lambda v: view.set_layer_visible(name, v))
        lay.addWidget(vis)

        # Opacite
        orow = QHBoxLayout()
        orow.addWidget(QLabel("Opacité"))
        op = QSlider(Qt.Horizontal)
        op.setRange(5, 100)
        op.setValue(int(round(items[0].opacity() * 100)))
        op.setFixedWidth(150)
        op.valueChanged.connect(lambda x: view.set_layer_opacity(name, x / 100.0))
        orow.addWidget(op)
        lay.addLayout(orow)

        # Ordre avant/arriere
        zrow = QHBoxLayout()
        zrow.addWidget(QLabel("Ordre"))
        back = QPushButton("Reculer")
        back.clicked.connect(lambda: view.move_layer(name, False))
        front = QPushButton("Avancer")
        front.clicked.connect(lambda: view.move_layer(name, True))
        zrow.addWidget(back); zrow.addWidget(front)
        lay.addLayout(zrow)

        # --- options specifiques : Footprint (donnees affichees) ---
        if name == "footprint":
            lay.addWidget(QLabel("Données affichées"))
            item = view.fp.item
            for opt, lbl in (("show_bars", "Barres bid/ask"),
                             ("show_poc", "POC")):
                c = QCheckBox(lbl)
                c.setChecked(getattr(item, opt))
                c.toggled.connect(lambda v, o=opt: view.set_footprint_option(o, v))
                lay.addWidget(c)
            lay.addWidget(QLabel("Caractères (indépendant)"))
            for opt, lbl in (("show_numbers", "Chiffres bid×ask"),
                             ("show_header", "En-tête V / D")):
                c = QCheckBox(lbl)
                c.setChecked(getattr(item, opt))
                c.toggled.connect(lambda v, o=opt: view.set_footprint_option(o, v))
                lay.addWidget(c)

        # --- options specifiques : Trades ---
        if name == "trades":
            drow = QHBoxLayout()
            drow.addWidget(QLabel("Taille points"))
            ds = QSlider(Qt.Horizontal)
            ds.setRange(30, 250)
            ds.setValue(int(round(view.dot_scale * 100)))
            ds.setFixedWidth(150)
            ds.valueChanged.connect(lambda x: view.set_dot_scale(x / 100.0))
            drow.addWidget(ds)
            lay.addLayout(drow)

            mrow = QHBoxLayout()
            mrow.addWidget(QLabel("Min size"))
            ms = QDoubleSpinBox()
            ms.setRange(0.0, 1e9); ms.setDecimals(3); ms.setSingleStep(0.1)
            ms.setValue(view.min_size)
            ms.valueChanged.connect(view.set_min_size)
            mrow.addWidget(ms)
            lay.addLayout(mrow)

            au = QCheckBox("Auto (limite la densité)")
            au.setChecked(view.auto_filter)
            au.toggled.connect(lambda v: setattr(view, "auto_filter", v))
            lay.addWidget(au)


class LayersPanel(_Popup):
    """Liste des types : case Visible + engrenage qui ouvre les reglages complets."""

    _ROWS = [("footprint", "Footprint"), ("heatmap", "Heatmap"),
             ("trades", "Trades"), ("dom", "Carnet")]

    def __init__(self, view) -> None:
        super().__init__()
        self.view = view
        self._subs: dict[str, TypeSettings] = {}
        self._anchor = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        for name, label in self._ROWS:
            row = QHBoxLayout()
            row.setSpacing(8)
            chk = QCheckBox(label)
            chk.setChecked(True)
            chk.setMinimumWidth(100)
            if name == "dom":
                chk.toggled.connect(view.set_dom_visible)
            else:
                chk.toggled.connect(lambda v, n=name: view.set_layer_visible(n, v))
            row.addWidget(chk)
            row.addStretch(1)
            gear = QPushButton("⚙")
            gear.setFixedWidth(30)
            gear.setToolTip("Paramètres")
            gear.clicked.connect(lambda _=False, n=name: self._open(n))
            row.addWidget(gear)
            lay.addLayout(row)

    def _open(self, name: str) -> None:
        self.hide()                      # un seul popup a la fois
        sub = self._subs.get(name)
        if sub is None:
            sub = TypeSettings(self.view, name)
            self._subs[name] = sub
        sub.show_under(self._anchor)

    def popup_under(self, button) -> None:
        self._anchor = button
        self.show_under(button)
