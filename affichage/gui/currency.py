"""Taux de change USD -> CAD pour l'affichage optionnel des prix en $CA.

Les prix internes restent en USD (USDT). La conversion n'est qu'un facteur
d'affichage applique sur l'axe des prix. Le taux est rafraichi en arriere-plan
(API gratuite, sans cle) ; repli sur une valeur par defaut si pas de reseau.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

_URL = "https://open.er-api.com/v6/latest/USD"


class FxRate:
    def __init__(self, default: float = 1.37) -> None:
        self.usd_cad = default
        threading.Thread(target=self._loop, name="fx", daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                with urllib.request.urlopen(_URL, timeout=8) as r:
                    rate = json.load(r).get("rates", {}).get("CAD")
                if rate:
                    self.usd_cad = float(rate)
            except Exception:  # noqa: BLE001 - reseau indisponible => on garde l'ancien
                pass
            time.sleep(3600)
