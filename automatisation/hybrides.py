# hybrides.py — moteur PARTAGÉ des 3 stratégies hybrides, porté À L'IDENTIQUE des jumeaux
# des indices (indicesBoursiers/backtesting/backtests/algorithms/sma_{bracket,suiveur,annule}_nq.py).
# Déclencheur COMMUN : croisement SMA 9/21 sur closes 1 m. Les 3 ne diffèrent que par la GESTION :
#
#   H1 SMA Bracket   : SL 1,5×ATR / TP 1R ; IGNORE les croisements en position (le bracket referme).
#   H2 SMA Suiveur   : SL 2×ATR, PAS de TP ; stop SUIVEUR (remonté chaque barre, ne recule jamais) ;
#                      sortie sur stop OU croisement inverse.
#   H3 SMA Annulation: SL 1,5×ATR / TP 2R ; sortie ANTICIPÉE au croisement inverse (annule le bracket).
#
# ATR = Wilder (comme LEAN), pour coller aux jumeaux des indices. Moteur PUR (aucun ordre) :
# il émet des événements (signal/entree/stop_modifie/sortie) via un callback `emit`. Utilisé par
# le backtest (jumeau_hybrides.py) ET par le runner live (shadow + go).
from __future__ import annotations

SMA_RAPIDE, SMA_LENTE, ATR_N = 9, 21, 14      # valeurs des indices
COOLDOWN_S = 120

# stop_mult ×ATR ; tp_r = multiple de R (=stop_mult×ATR), None si pas de TP ; suiveur ; sortie au
# croisement inverse. (Repris tel quel des 3 jumeaux NQ.)
CONFIGS = {
    "h1": dict(slug="sma_bracket", nom="SMA Bracket",    stop_mult=1.5, tp_r=1.0,  suiveur=False, sortie_croix=False),
    "h2": dict(slug="sma_suiveur", nom="SMA Suiveur",    stop_mult=2.0, tp_r=None, suiveur=True,  sortie_croix=True),
    "h3": dict(slug="sma_annule",  nom="SMA Annulation", stop_mult=1.5, tp_r=2.0,  suiveur=False, sortie_croix=True),
}


class MoteurHybride:
    def __init__(self, strat, rapide=SMA_RAPIDE, lente=SMA_LENTE, atr_n=ATR_N,
                 cooldown_s=COOLDOWN_S, emit=None, gerer_sl_tp=True):
        if strat not in CONFIGS:
            raise ValueError(f"stratégie inconnue {strat} (h1/h2/h3)")
        self.cfg = CONFIGS[strat]
        self.strat = strat
        self.rapide, self.lente, self.atr_n = rapide, lente, atr_n
        self.cooldown_s = cooldown_s
        self.emit = emit or (lambda *a, **k: None)
        # True (shadow/backtest) : le moteur SIMULE les touches SL/TP sur les high/low.
        # False (--go) : le bracket SERVEUR gère SL/TP → le runner appelle fermeture_externe.
        self.gerer_sl_tp = gerer_sl_tp
        self.closes: list[float] = []
        self.diff_prec = None
        self._prev_close = None
        self._trs: list[float] = []
        self._atr = None
        # position
        self.pos = 0                 # +1 long, -1 short, 0 plat
        self.entree = self.stop = self.take = self.extreme = self.atr_entree = None
        self.cooldown_jusqu = 0
        self.dernier: dict | None = None   # dernier état (pour le battement de cœur)

    # --- indicateurs ---
    def _maj_atr(self, h, l, c):
        tr = (h - l) if self._prev_close is None else max(
            h - l, abs(h - self._prev_close), abs(l - self._prev_close))
        self._prev_close = c
        self._trs.append(tr)
        if len(self._trs) < self.atr_n:
            self._atr = None
        elif self._atr is None:
            self._atr = sum(self._trs[-self.atr_n:]) / self.atr_n     # amorce = SMA des TR
        else:
            self._atr = (self._atr * (self.atr_n - 1) + tr) / self.atr_n   # Wilder
        return self._atr

    # --- une barre 1 m close ---
    def barre(self, ts, o, h, l, c, amorce=False):
        """ts = horodatage (ms ou ISO, opaque, repassé tel quel aux événements).
        amorce=True : chauffe seulement les indicateurs (SMA/ATR/diff_prec), SANS trader —
        pour gaver l'historique fermé au démarrage du runner live (sinon l'ATR n'est prêt
        qu'après ~14 barres de temps réel et les 1ers croisements sont ratés)."""
        self.closes.append(c)
        atr = self._maj_atr(h, l, c)
        if len(self.closes) < self.lente + 1:
            return
        sr = sum(self.closes[-self.rapide:]) / self.rapide
        sl = sum(self.closes[-self.lente:]) / self.lente
        diff = sr - sl
        croix = 0
        if self.diff_prec is not None:
            if self.diff_prec <= 0 < diff:
                croix = 1
            elif self.diff_prec >= 0 > diff:
                croix = -1
        self.diff_prec = diff
        self.dernier = dict(ts=ts, close=c, sr=sr, sl=sl, diff=diff, atr=atr,
                            croix=croix, pos=self.pos)
        if amorce:
            return
        if self.pos != 0:
            self._gerer(ts, h, l, c, atr, croix)
        else:
            self._entrer(ts, c, atr, croix, sr, sl)

    # --- gestion d'une position ouverte (ordre EXACT des jumeaux) ---
    def _gerer(self, ts, h, l, c, atr, croix):
        long = self.pos > 0
        self.extreme = max(self.extreme, h) if long else min(self.extreme, l)
        # 1) stop / TP (SL prioritaire, comme les jumeaux) — SIMULÉ seulement hors --go
        #    (en --go, c'est le bracket serveur qui referme -> fermeture_externe).
        if self.gerer_sl_tp:
            if long and l <= self.stop:
                return self._sortir(ts, "SL", self.stop)
            if not long and h >= self.stop:
                return self._sortir(ts, "SL", self.stop)
            if self.take is not None:
                if long and h >= self.take:
                    return self._sortir(ts, "TP", self.take)
                if not long and l <= self.take:
                    return self._sortir(ts, "TP", self.take)
        # 2) croisement inverse (H2/H3)
        if self.cfg["sortie_croix"] and ((long and croix < 0) or (not long and croix > 0)):
            self.emit("signal", ts, prix=round(c, 1), sens="short" if long else "long",
                      raison="croisement inverse -> sortie")
            return self._sortir(ts, "SIGNAL", c, raison="croisement inverse")
        # 3) stop suiveur (H2) — ne recule jamais
        if self.cfg["suiveur"] and atr is not None:
            cand = (self.extreme - self.cfg["stop_mult"] * atr if long
                    else self.extreme + self.cfg["stop_mult"] * atr)
            if (long and cand > self.stop) or (not long and cand < self.stop):
                self.stop = cand
                self.emit("stop_modifie", ts, prix=round(self.stop, 1), extreme=round(self.extreme, 1),
                          atr=round(atr, 1), raison="suiveur")

    def _sortir(self, ts, code, niveau, raison=None):
        self.emit("sortie", ts, prix=round(niveau, 1), code=code,
                  raison=raison or f"{code} touché")
        self.pos = 0
        self.entree = self.stop = self.take = self.extreme = None
        self.cooldown_jusqu = self._ts_s(ts) + self.cooldown_s

    def annuler_entree(self, ts):
        """Le runner refuse l'entrée (garde-fou dislocation) : annule la position tout juste
        ouverte et met le cooldown (évite de re-tenter au croisement suivant immédiat)."""
        self.pos = 0
        self.entree = self.stop = self.take = self.extreme = None
        self.cooldown_jusqu = self._ts_s(ts) + self.cooldown_s

    def fermeture_externe(self, ts, code="SLTP", prix=None):
        """--go : le bracket SERVEUR a refermé la position (SL/TP). Le runner le constate
        (compte à plat) et le signale ici pour resynchroniser le moteur."""
        if self.pos == 0:
            return
        self._sortir(ts, code, prix if prix is not None else (self.stop or 0),
                     raison="bracket serveur refermé (SL/TP)")

    # --- entrée sur croisement ---
    def _entrer(self, ts, c, atr, croix, sr, sl):
        if croix == 0 or atr is None or self._ts_s(ts) < self.cooldown_jusqu:
            return
        self.pos = 1 if croix > 0 else -1
        sens = "long" if croix > 0 else "short"
        self.entree = c
        self.extreme = c
        self.atr_entree = atr
        r = self.cfg["stop_mult"] * atr
        self.stop = c - r if croix > 0 else c + r
        self.take = None if self.cfg["tp_r"] is None else (
            c + self.cfg["tp_r"] * r if croix > 0 else c - self.cfg["tp_r"] * r)
        self.emit("signal", ts, prix=round(c, 1), sens=sens,
                  raison=f"croisement {'haussier' if croix > 0 else 'baissier'} -> {sens}",
                  sma_rapide=round(sr, 1), sma_lente=round(sl, 1), atr=round(atr, 1))
        self.emit("entree", ts, prix=round(c, 1), sens=sens, sl=round(self.stop, 1),
                  tp=None if self.take is None else round(self.take, 1),
                  raison=f"market {sens} + " + ("SL seul (suiveur)" if self.cfg["suiveur"] else "bracket"))

    @staticmethod
    def _ts_s(ts):
        """Secondes epoch depuis un ts en ms (int) ou ISO (str) — pour le cooldown."""
        if isinstance(ts, (int, float)):
            return ts / 1000.0
        from datetime import datetime
        return datetime.fromisoformat(ts).timestamp()
