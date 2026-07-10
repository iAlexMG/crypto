"""Configuration du logging du dashboard.

Log de DEBOGAGE uniquement : un seul fichier `logs/crypto.log` ECRASE a chaque
lancement (mode 'w') — inutile de garder l'historique des sessions. On ne logge
JAMAIS les données de marché (contenu des books/trades) — uniquement les
ÉVÈNEMENTS : connexions, souscriptions, watchdog, resync, checksum, backfill, et
la santé périodique par flux (cf. app._log_health).

NB : la console reçoit pour l'instant la MÊME chose que le fichier (pratique en
dev). A terme, ces sorties terminal seront supprimées (le programme fini n'en a
plus besoin).
"""
from __future__ import annotations

import logging
import os


def setup_logging(level: int = logging.INFO) -> str:
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", "crypto.log")
    root = logging.getLogger()
    root.setLevel(level)
    if any(getattr(h, "_crypto", False) for h in root.handlers):
        return path                                   # deja configure (appel double)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(path, mode="w", encoding="utf-8")   # ECRASE a chaque lancement
    fh.setFormatter(fmt); fh._crypto = True           # type: ignore[attr-defined]
    ch = logging.StreamHandler()
    ch.setFormatter(fmt); ch._crypto = True           # type: ignore[attr-defined]
    root.addHandler(fh)
    root.addHandler(ch)
    logging.getLogger("websockets").setLevel(logging.WARNING)   # coupe le bruit bas niveau
    return path
