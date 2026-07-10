"""Interface commune a tous les connecteurs d'exchange.

Chaque exchange gere SES particularites en interne (init orderbook WS-only vs
REST+WS, checksum/sequence, format des symboles) et n'expose que des donnees
deja normalisees, poussees dans le MarketHub.

La profondeur est configurable PAR connecteur : le nombre de niveaux
demande influence la frequence des updates, donc chaque exchange choisit la
profondeur qui optimise sa synchronisation.
"""
from __future__ import annotations

import abc
import asyncio
import logging

from ..hub import MarketHub

log = logging.getLogger("connector")


class BaseConnector(abc.ABC):
    name: str = "base"

    def __init__(self, hub: MarketHub, symbols: list[str], depth: int) -> None:
        self.hub = hub
        self.symbols = symbols
        self.depth = depth
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Boucle de vie : (re)connexion avec backoff exponentiel borne."""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                log.info("[%s] connexion...", self.name)
                await self._stream()
                backoff = 1.0  # reset apres une session saine
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - on veut survivre a tout
                log.warning("[%s] deconnecte: %s (retry %.0fs)", self.name, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def stop(self) -> None:
        self._stop.set()

    @abc.abstractmethod
    async def _stream(self) -> None:
        """Ouvre la connexion WS, amorce le carnet, et pousse les donnees
        normalisees vers le hub jusqu'a deconnexion."""
        raise NotImplementedError
