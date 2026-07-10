"""Collecteur d'historique des trades (UN thread par EXCHANGE) -> trades.db.

Lance avec le GUI (pas de programme separe), il s'arrete a la fermeture et REPREND
a la session suivante.

PARALLELISME PAR EXCHANGE : chaque exchange (Bitget, Binance, Bybit, OKX) a son
PROPRE worker (thread). Les exchanges tapent des serveurs et des limites de debit
INDEPENDANTS -> un exchange lent ou rate-limite (429) n'affame plus les autres
(c'etait le defaut de la boucle unique : le gros backlog Bitget retardait OKX).
Le travail est I/O-bound (REST + SQLite) : des THREADS suffisent (le GIL est relache
sur les I/O) ; pas besoin de processus. SQLite reste mono-ecrivain (verrou de
l'archive) -> on ne multiplie pas les ecrivains, juste les producteurs reseau.
Chaque worker ne touche QUE ses flux (key, sym) -> dicts d'etat surs sans verrou.
Sur erreur reseau/429, le flux fautif est mis en COOLDOWN (COOLDOWN_S) le temps que
le worker serve ses autres flux. Dans chaque worker, deux phases dans l'ordre :

PHASE A - COMBLER LES TROUS (prioritaire), de facon PERSISTANTE. Au fil des sessions
  (app lancee/arretee), l'archive accumule des TROUS : entre CHAQUE arret et reprise,
  et a chaque reconnexion watchdog (il peut donc y en avoir PLUSIEURS). `find_holes`
  les detecte TOUS (tout ecart > seuil entre deux trades consecutifs), aussi anciens
  soient-ils. On comble EN BOUCLE jusqu'a ce qu'il n'en reste plus de comblable :
    - BINANCE -> SEEK DIRECT par temps (fetch_range/startTime : saut au trou) ;
    - BITGET  -> PARCOURS ARRIERE (fetch_before, chaine les ids REST ; pas de startTime).
  On n'insere que les trades DANS un trou (INSERT OR IGNORE -> pas de double-comptage).
  Un trou qui ne PROGRESSE plus (pas de data REST) est mis de cote, puis RE-ESSAYE
  periodiquement (GAP_RESCAN_S) en meme temps qu'on re-scanne pour de NOUVEAUX trous.

PHASE B - REMONTER LE PASSE. Une fois les trous combles, on etend l'historique
  profond en arriere (REST `fetch_before`), en repartant du trade le plus ancien.
  Bitget s'arrete vers ~90j ; Binance jusqu'au listing.

Le flux AFFICHE est traite EN PRIORITE (l'historique grandit la ou on regarde).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from backend.archive import TradeArchive
from backend.hub import MarketHub

log = logging.getLogger("collector")

# au-dela de cet ecart entre deux trades consecutifs -> TROU (app eteinte ou coupure).
# Regle utilisateur : >= 30 s sans trade = trou A COMBLER. Regle FAILLIBLE : un creux
# de marche reel (aucun trade) existe ; si une requete REST sur ce trou ne ramene rien
# DEDANS, on ne s'acharne pas (le trou est marque VIDE -> plus retente, cf. _empty).
HOLE_THRESHOLD_MS = 30_000

# Une fois qu'un flux n'a plus de trou COMBLABLE (historique complet, ou trous sans
# data REST), on re-scanne quand meme tout periodiquement : capte les nouveaux trous
# (reconnexions de la session courante) et reessaie les trous restes incomblables.
GAP_RESCAN_S = 180.0                  # frequence de re-scan complet d'un flux "settled"

# Bitget comble un trou par fetch_before AMORCE au bord superieur du trou (cf.
# _fill_gaps). Sans borne, un seul appel `_fill_gaps` enchainerait toutes les pages du
# trou et monopoliserait la boucle -> AFFAME les autres flux. On BORNE donc chaque passe
# a ce nombre de pages ; le reste est repris a la passe suivante (find_holes recalcule
# le trou retreci -> nouveau bord). Cooperatif + reprenable, sans etat en memoire.
GAP_FILL_MAX_PAGES = 8

# Le flux AFFICHE (ce que l'utilisateur regarde) recoit un budget BIEN plus large :
# sinon un gros trou (app restee fermee des heures) se comble par paliers de 8 pages
# noyes parmi les 16 flux -> l'historique du flux regarde n'apparait jamais dans une
# session. Avec ce budget, le trou RECENT (haut du trou, comble en premier) du flux
# affiche se remplit en un ou deux passages. Les flux NON affiches restent a
# GAP_FILL_MAX_PAGES (cooperatif, pas de famine).
GAP_FILL_MAX_PAGES_ACTIVE = 40

# Apres une erreur reseau / 429 (rate-limit) sur un flux, on le met en PAUSE ce
# delai : le worker passe aux autres flux du meme exchange au lieu de re-taper
# l'endpoint qui vient de jeter -> evite d'aggraver un rate-limit.
COOLDOWN_S = 30.0

# (cle hub, exchange, tag archive, fetch_before(sym, before_id), fetch_range(sym,a,b)|None)
# exchange = libelle ("Bitget", "OKX"...) : sert a REGROUPER les flux par exchange ->
# UN worker (thread) par exchange (serveurs/limites independants, parallelisme reseau
# reel, un exchange lent n'affame plus les autres).
# fetch_range = SEEK DIRECT par temps pour combler un trou (Binance) ; None = pas de
# seek (Bitget/OKX) -> on garde le parcours arriere fetch_before.
CollectorFeed = tuple[str, str, str, Callable[[str, "str | None"], list], "Callable | None"]


class HistoryCollector:
    def __init__(self, archive: TradeArchive, hub: MarketHub,
                 feeds: list[CollectorFeed], symbols: list[str],
                 startup_newest: dict[tuple[str, str], int | None] | None = None,
                 base_exchange: str = "Bitget", base_tag: str = "USDT-FUTURES",
                 pause: float = 0.3) -> None:
        self.archive = archive
        self.hub = hub
        # BITGET = base de tout : on ANCRE l'historique de tous les exchanges sur le
        # trade Bitget le plus ancien (le "plancher"). Un autre exchange ne descend
        # JAMAIS sous ce plancher, meme s'il a des donnees plus anciennes -> on les
        # ignore tant que Bitget n'a pas recouvert une periode encore plus ancienne.
        # base_tag = flux Bitget de reference (futures) ou lire le plancher.
        self.base_exchange = base_exchange
        self.base_tag = base_tag
        # REGROUPEMENT par exchange -> un worker par exchange. Chaque worker possede
        # EXCLUSIVEMENT ses flux (key, sym) : les dicts d'etat partages ci-dessous
        # sont donc surs sans verrou (une cle n'est touchee que par un seul thread).
        self._by_exchange: dict[str, list[tuple]] = {}
        self._exchange_of: dict[str, str] = {}     # cle hub -> libelle exchange
        for key, exchange, tag, fb, fr in feeds:
            self._by_exchange.setdefault(exchange, []).append((key, tag, fb, fr))
            self._exchange_of[key] = exchange
        self.symbols = list(symbols)
        # ts du dernier trade archive AVANT cette session (= fin de la session
        # precedente), capture au demarrage avant toute ecriture live/backfill.
        # Sert a borner le TROU DE TETE de facon fiable (independamment du timing
        # de l'archivage live). None = rien d'archive avant.
        self.startup_newest = dict(startup_newest or {})
        self.pause = pause
        self.active: tuple[str, str] | None = None   # (cle, symbole) affiche
        self._cursor: dict[tuple[str, str], str | None] = {}
        self._seeded: set[tuple[str, str]] = set()
        self._done: set[tuple[str, str]] = set()        # phase B epuisee
        # PHASE A = comblage PERSISTANT : on comble en boucle jusqu'a ce que
        # find_holes ne renvoie plus rien (ou que plus aucun trou ne progresse).
        # _settled = ce flux n'a plus de trou comblable POUR L'INSTANT (-> PHASE B) ;
        # re-ouvert periodiquement (_rescan_at) pour capter de nouveaux trous /
        # reessayer. _last_sig = derniere signature de trous tentee (no-progress).
        self._settled: set[tuple[str, str]] = set()
        self._last_sig: dict[tuple[str, str], tuple] = {}
        self._rescan_at: dict[tuple[str, str], float] = {}
        self._cooldown: dict[tuple[str, str], float] = {}   # fk -> deadline monotonic
        # Trous confirmes VIDES (REST a montre qu'aucun trade n'a eu lieu dedans) :
        # (tag, sym, a, b). On ne les retente plus -> "ne pas s'acharner" (regle 30 s
        # faillible). Stable car a/b sont des trades archives permanents.
        self._empty: set[tuple[str, str, int, int]] = set()
        self._stop = False
        # UN thread par exchange (parallelisme reseau ; isole les 429 / pannes).
        self._threads = [
            threading.Thread(target=self._run_worker, args=(ex, feeds),
                             name=f"collector-{ex}", daemon=True)
            for ex, feeds in self._by_exchange.items()
        ]

    def start(self) -> None:
        for t in self._threads:
            t.start()

    def set_active(self, key: str, symbol: str) -> None:
        """Flux AFFICHE -> traite en priorite (l'historique grandit la ou on regarde)."""
        self.active = (key, symbol)

    def stop(self) -> None:
        self._stop = True

    def _run_worker(self, exchange: str, feeds: list[tuple]) -> None:
        """Boucle d'UN exchange (thread dedie). Ne traite QUE ses flux -> un exchange
        lent ou rate-limite n'affame pas les autres. Flux AFFICHE en tete s'il
        appartient a cet exchange (priorite a l'historique regarde)."""
        # attendre que le live alimente le hub (connexion WS) -> "debut du live"
        # fiable pour borner le trou de tete sans doublonner ce que le live archive.
        self._await_live(8.0, feeds)
        while not self._stop:
            progressed = False
            for key, tag, sym, fb, fr in self._worker_streams(feeds):
                if self._stop:
                    return
                if self._collect_feed(key, tag, sym, fb, fr):
                    progressed = True
                time.sleep(self.pause)
            if not progressed:
                for _ in range(20):            # tout epuise -> dort jusqu'a l'arret
                    if self._stop:
                        return
                    time.sleep(0.5)

    def _cool(self, key: str, sym: str) -> None:
        """Met ce flux en pause apres une erreur reseau/429 (anti-aggravation)."""
        self._cooldown[(key, sym)] = time.monotonic() + COOLDOWN_S

    def _floor(self, key: str, sym: str) -> int | None:
        """Plancher d'historique pour ce flux = trade Bitget le plus ancien (base).
        None pour les flux Bitget eux-memes (ils DEFINISSENT le plancher -> pas de
        borne). Pour les autres : on n'ira pas chercher d'historique sous ce ts."""
        if self._exchange_of.get(key) == self.base_exchange:
            return None
        return self.archive.earliest(self.base_tag, sym)

    def _collect_feed(self, key: str, tag: str, sym: str,
                      fetch_before, fetch_range) -> bool:
        """Un pas de travail pour UN flux. Renvoie True si quelque chose a progressé.
        PHASE A PERSISTANTE : tant qu'il reste un trou COMBLABLE on le comble (en
        boucle, find_holes recalcule -> on capte le reste). Quand plus rien ne
        progresse (historique complet, ou trous sans data REST) le flux est "settled"
        et passe en PHASE B ; on le re-scanne quand même tous les GAP_RESCAN_S."""
        fk = (key, sym)
        now = time.monotonic()
        if now < self._cooldown.get(fk, 0.0):     # en pause apres une erreur/429
            return False
        # re-ouvre periodiquement le comblage (nouveaux trous de reconnexion / retry).
        if fk in self._settled and now >= self._rescan_at.get(fk, 0.0):
            self._settled.discard(fk)
        if fk not in self._settled:
            holes = self._gaps_for(key, tag, sym)
            sig = tuple(holes)
            if not holes:
                self._settled.add(fk)                    # historique COMPLET
                self._rescan_at[fk] = now + GAP_RESCAN_S
            elif sig == self._last_sig.get(fk):
                # le dernier remplissage n'a RIEN changé -> incomblable pour l'instant
                # (pas de data REST). On attend le prochain re-scan.
                self._settled.add(fk)
                self._rescan_at[fk] = now + GAP_RESCAN_S
            elif self._fill_gaps(key, tag, sym, fetch_before, fetch_range, holes,
                                 max_pages=self._gap_budget(fk)):
                self._last_sig[fk] = sig                 # tenté -> on reboucle pour le reste
                return True
            return False                                 # (erreur réseau -> réessai)
        if fk not in self._done:
            return self._step_back(fk, tag, sym, fetch_before)   # PHASE B
        return False

    def _worker_streams(self, feeds: list[tuple]) -> list[tuple]:
        """(cle, tag, symbole, fetch_before, fetch_range|None) pour les flux d'UN
        exchange, flux AFFICHE en tete s'il appartient a cet exchange."""
        out = [(key, tag, sym, fb, fr)
               for key, tag, fb, fr in feeds for sym in self.symbols]
        act = self.active
        if act is not None:
            out.sort(key=lambda x: 0 if (x[0], x[2]) == act else 1)
        return out

    def _await_live(self, timeout: float, feeds: list[tuple]) -> None:
        deadline = time.monotonic() + timeout
        while not self._stop and time.monotonic() < deadline:
            for key, _tag, _fb, _fr in feeds:
                for sym in self.symbols:
                    if self.hub.last_trade_ts(key, sym):
                        return
            time.sleep(0.25)

    # -- PHASE A : combler TOUS les trous (du present au plus ancien) --------
    def _gaps_for(self, key: str, tag: str, sym: str,
                  include_head: bool = True) -> list[tuple[int, int]]:
        """TOUS les trous (a, b) a combler, du present au plus ancien : trous internes
        (detectes en SQL) + (si include_head) trou de tete (fin session precedente ->
        1er trade live, comble tot avant meme que le live ne soit archive). Le scan est
        borne au PLANCHER Bitget pour les autres exchanges (on ne comble pas sous le
        plancher) -> aussi une borne de cout. Les trous confirmes VIDES (_empty) sont
        exclus : on ne s'acharne pas dessus."""
        since = self._floor(key, sym)
        gaps = list(self.archive.find_holes(tag, sym, HOLE_THRESHOLD_MS, since_ms=since))
        if include_head:
            newest = self.startup_newest.get((tag, sym))
            if newest is not None:
                live = self.hub.trades_since(key, sym, 0)
                if live and live[0].ts > newest + HOLE_THRESHOLD_MS:
                    gaps.append((newest, live[0].ts))   # trou de tete (borne fiable)
        return [g for g in gaps if (tag, sym, g[0], g[1]) not in self._empty]

    def _gap_budget(self, fk: tuple[str, str]) -> int:
        """Pages de comblage par passe : large pour le flux AFFICHE (apparait vite),
        cooperatif (GAP_FILL_MAX_PAGES) pour les autres."""
        return GAP_FILL_MAX_PAGES_ACTIVE if fk == self.active else GAP_FILL_MAX_PAGES

    def _fill_gaps(self, key: str, tag: str, sym: str,
                   fetch_before: Callable[[str, "str | None"], list],
                   fetch_range: "Callable | None",
                   gaps: list[tuple[int, int]],
                   max_pages: int = GAP_FILL_MAX_PAGES) -> bool:
        """Comble la liste `gaps`. Si `fetch_range` est dispo (seek par temps,
        Binance) -> SAUT DIRECT a chaque trou (rapide, pas de re-parcours depuis le
        present, progres persiste dans l'archive). Sinon (Bitget) -> parcours arriere
        REST en chainant les ids. Renvoie True si la passe s'est achevee sans erreur
        reseau (False -> a reessayer, ne pas marquer le flux traite)."""
        if not gaps:
            return True
        if fetch_range is not None:
            return self._fill_gaps_seek(key, tag, sym, fetch_range, gaps)
        # BITGET : Fills-History n'a pas de startTime -> pas de seek. Mais l'id WS == l'id
        # REST (docs/bitget.md), donc on AMORCE fetch_before au BORD SUPERIEUR du trou
        # (id archive du 1er trade APRES la coupure) et on descend dedans : insertion
        # IMMEDIATE, plus de re-parcours des ~heures de zone deja pleine (c'etait le
        # gaspillage Bitget : le flux le plus volumineux n'atteignait jamais son trou).
        # Borne a GAP_FILL_MAX_PAGES pages/passe (cooperatif -> n'affame pas les autres
        # flux) ; le reste est REPRIS a la passe suivante (find_holes recalcule le trou
        # retreci -> nouveau bord) et MEME entre sessions (l'archive est l'etat, aucun
        # curseur en memoire). Trous RECENTS d'abord (l'historique proche se remplit en 1er).
        total = 0
        pages = 0
        for a, b in sorted(gaps, reverse=True):
            if self._stop or pages >= max_pages:
                break
            before = self.archive.id_at_or_after(tag, sym, b)   # bord superieur du trou
            if before is None:                                   # trou de tete (pas encore
                continue                                         # de trade apres) -> plus tard
            gap_kept = 0                           # trades inseres DANS ce trou precis
            while not self._stop and pages < max_pages:
                try:
                    batch = fetch_before(sym, before)
                except Exception as exc:  # noqa: BLE001 - reseau -> on reessaiera + tard
                    log.warning("gap-fill %s %s: %s", key, sym, exc)
                    self._cool(key, sym)
                    return False
                pages += 1
                if not batch:
                    break
                keep = [t for t in batch if a < t.ts < b]       # uniquement DANS ce trou
                if keep:
                    self.archive.insert(tag, keep)
                    total += len(keep)
                    gap_kept += len(keep)
                oldest = batch[0]                  # batch trie par ts croissant
                if oldest.ts <= a:                 # trou ENTIEREMENT parcouru
                    if gap_kept == 0:              # ... et AUCUN trade dedans -> VIDE :
                        self._empty.add((tag, sym, a, b))   # ne plus s'acharner dessus
                    break
                if oldest.trade_id == before:      # pas de progres (pas de data REST)
                    break
                before = oldest.trade_id           # id REST == id WS -> espace correct
                time.sleep(self.pause)
        if total:
            log.info("gap-fill %s %s: +%d trades (%d trous)", key, sym, total, len(gaps))
        return True

    def _fill_gaps_seek(self, key: str, tag: str, sym: str,
                        fetch_range: Callable[[str, int, int], list],
                        gaps: list[tuple[int, int]]) -> bool:
        """Comble chaque trou par SEEK DIRECT (startTime) : on saute a son debut et
        on pagine vers l'avant jusqu'a le couvrir. Pas de re-parcours de la zone
        hors-trou (contrairement a fetch_before) -> rapide meme pour un trou lointain.
        Le progres PERSISTE dans l'archive : si un trou n'est que partiellement comble
        (gros trou), la passe suivante repart de ce qui reste (find_holes recalcule).
        Trous traites du PLUS RECENT au plus ancien (l'historique proche d'abord)."""
        total = 0
        for a, b in sorted(gaps, reverse=True):
            if self._stop:
                return False
            try:
                batch = fetch_range(sym, a, b)
            except Exception as exc:  # noqa: BLE001 - reseau -> on reessaiera + tard
                log.warning("gap-seek %s %s: %s", key, sym, exc)
                self._cool(key, sym)
                return False
            keep = [t for t in batch if a < t.ts < b]
            if keep:
                self.archive.insert(tag, keep)
                total += len(keep)
            time.sleep(self.pause)
        if total:
            log.info("gap-seek %s %s: +%d trades (%d trous)", key, sym, total, len(gaps))
        return True

    # -- PHASE B : remonter le passe profond (borne au PLANCHER Bitget) -----
    def _step_back(self, fk: tuple[str, str], tag: str, sym: str,
                   fetch_before: Callable[[str, "str | None"], list]) -> bool:
        key = fk[0]
        # PLANCHER Bitget : un exchange non-base ne descend pas sous le trade Bitget le
        # plus ancien. S'il l'a deja atteint -> on s'arrete SANS marquer _done (Bitget
        # peut descendre plus bas plus tard -> on reprendra alors, sans requete REST
        # inutile entre-temps). Bitget lui-meme : floor=None -> remonte librement.
        floor = self._floor(key, sym)
        if floor is not None:
            earliest = self.archive.earliest(tag, sym)
            if earliest is not None and earliest <= floor:
                return False                  # au plancher -> on attend que Bitget descende
        if fk not in self._seeded:
            self._cursor[fk] = self.archive.earliest_id(tag, sym)
            self._seeded.add(fk)
        before = self._cursor[fk]
        try:
            batch = fetch_before(sym, before)
        except Exception as exc:  # noqa: BLE001 - reseau/API -> on reessaiera plus tard
            log.warning("collector %s %s: %s", key, sym, exc)
            self._cool(key, sym)
            return False
        if not batch:
            self._done.add(fk)
            return False
        oldest = batch[0].trade_id            # batch trie par ts croissant
        if oldest == before:                  # aucun progres -> on arrete ce flux
            self._done.add(fk)
            return False
        if floor is not None:                 # ne garder que ce qui est >= plancher
            kept = [t for t in batch if t.ts >= floor]
            if not kept:
                # On a fetche de VRAIS trades plus anciens, mais TOUS sous le plancher
                # Bitget. Le curseur ne doit PAS avancer (on veut re-tenter quand Bitget
                # descendra). Sans garde, on re-fetcherait la MEME page a chaque tour de
                # worker -> 1 requete REST gaspillee par cycle (revue #3). COOLDOWN : on
                # attend avant de retester (le plancher ne descend qu'au rythme de Bitget).
                self._cool(key, sym)
                return False                  # tout est sous le plancher -> on attend Bitget
            batch = kept
        self.archive.insert(tag, batch)
        self._cursor[fk] = batch[0].trade_id  # plus ancien conserve
        return True
