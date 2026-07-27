# runner_sma.py — RUNNER LIVE des 3 hybrides (H1/H2/H3), via le moteur PARTAGÉ hybrides.py
# (identique aux jumeaux des indices). Signal SMA cross sur données BINANCE → exécution sur
# BITGET démo. Objectif = prouver la CHAÎNE des 3 stratégies (pas la rentabilité).
#
# Modes (comme les indices) :
#   défaut = SHADOW : le moteur SIMULE tout (SL/TP/suiveur/annulation), journalise, AUCUN ordre ;
#   --go            = exécute sur la DÉMO Bitget : le bracket SL/TP est SERVEUR ; le moteur pilote
#                     l'entrée, le stop suiveur (H2, modif serveur) et la sortie au croisement
#                     inverse (H2/H3, close) ; la fermeture SL/TP est constatée via le compte.
#
# Garde-fous : DÉMO uniquement, garde-fou dislocation (refuse l'entrée), kill switch Ctrl-C.
#
# Usage :
#   python runner_sma.py --strategie h1                 # SHADOW, H1
#   python runner_sma.py --strategie h2 --go            # exécute H2 (suiveur) sur la démo
#   python runner_sma.py --strategie h3 --go --rapide 3 --lente 9   # démo rapide
from __future__ import annotations

import argparse
import atexit
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import basis_dislocation
from hybrides import MoteurHybride, CONFIGS, SMA_RAPIDE, SMA_LENTE

BINANCE = "https://fapi.binance.com/fapi/v1/klines"
SYMBOL_BINANCE = SYMBOL_BITGET = "BTCUSDT"
JOURNAL_BASE = Path(__file__).resolve().parent / "journaux"

# Cadence : on ne sonde plus toutes les 10 s (ça décalait la réaction de 0-10 s après la
# clôture de la chandelle 1 m — stop suiveur qui « traînait »). On se CALE sur la frontière de
# minute : réveil ~1 s après le close, avec une courte boucle de récupération si la barre fermée
# tarde à être servie par Binance. Effet : le stop bouge ~1-2 s après la chandelle, pas 10-15 s.
BUFFER_S = 1.0    # marge après :00 (le temps que la barre fermée soit servie)
RETRIES = 6       # tentatives espacées de 1 s si la barre fermée tarde

# Verrou « une seule instance --go » : les 3 stratégies tradent le MÊME compte + symbole démo en
# one-way (position NETTE) → deux runners --go en parallèle EMPILENT les positions (bug constaté le
# 07-27 : un 2e runner laissé ouvert a doublé un short). Le PID est déposé dans un fichier sous
# journaux/ (donc hors git) ; un 2e runner refuse de démarrer tant que le 1er vit. Un verrou périmé
# (process mort) ou --force le reprend. SHADOW n'est pas concerné (aucun ordre).
VERROU = JOURNAL_BASE / ".runner-go.lock"


def _pid_vivant(pid):
    """Vrai si le process `pid` tourne encore (Windows via OpenProcess, repli POSIX os.kill)."""
    if pid <= 0:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259      # STILL_ACTIVE (sinon = process déjà sorti)
    except (AttributeError, OSError):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def acquerir_verrou(force=False):
    """Prend le verrou instance-unique. Renvoie (ok, pid_de_l_autre_si_refus)."""
    VERROU.parent.mkdir(parents=True, exist_ok=True)
    if VERROU.exists():
        try:
            autre = int(VERROU.read_text(encoding="utf-8").strip() or 0)
        except (ValueError, OSError):
            autre = 0
        if autre and autre != os.getpid() and _pid_vivant(autre) and not force:
            return False, autre
    VERROU.write_text(str(os.getpid()), encoding="utf-8")
    return True, None


def liberer_verrou():
    """Retire le verrou s'il est le nôtre (appelé à la sortie via atexit)."""
    try:
        if VERROU.exists() and VERROU.read_text(encoding="utf-8").strip() == str(os.getpid()):
            VERROU.unlink()
    except OSError:
        pass


def secondes_avant_minute(buffer=BUFFER_S):
    """Secondes jusqu'à la prochaine frontière de minute (+ marge)."""
    return (60 - (time.time() % 60)) + buffer


def klines_binance(limit=60):
    url = f"{BINANCE}?" + urllib.parse.urlencode(
        {"symbol": SYMBOL_BINANCE, "interval": "1m", "limit": limit})
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)[:-1]        # retire la bougie en cours


class Journal:
    def __init__(self, slug, mode):
        self.dir = JOURNAL_BASE / slug
        self.dir.mkdir(parents=True, exist_ok=True)
        self.slug, self.mode = slug, mode
        self._f = self._jour = None

    def ecrire(self, evenement, ts_ms, **champs):
        t = datetime.fromtimestamp(ts_ms / 1000, timezone.utc)
        if t.date() != self._jour:
            if self._f:
                self._f.close()
            self._jour = t.date()
            self._f = open(self.dir / f"{t.date():%Y-%m-%d}.ndjson", "a", encoding="utf-8")
        rec = {"ts": t.isoformat(), "strategie": self.slug, "symbole": SYMBOL_BITGET,
               "mode": self.mode, "evenement": evenement, **champs}
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._f.flush()
        det = " ".join(f"{k}={v}" for k, v in champs.items()
                       if k in ("raison", "prix", "sens", "sl", "tp", "code"))
        # journal en UTC (ci-dessus, non ambigu) ; AFFICHAGE en heure locale (Québec), pour coller
        # à l'horloge de Bitget à l'écran. fromtimestamp() sans tz = heure locale de l'OS (DST géré).
        t_local = datetime.fromtimestamp(ts_ms / 1000)
        print(f"  [{t_local:%H:%M:%S}] {evenement} {det}")


def _position_ouverte(client):
    return any(float(p.get("total", 0)) for p in client.positions(SYMBOL_BITGET))


def lancer(strat, go, size, lever, rapide, lente, force=False):
    if go:
        ok, autre = acquerir_verrou(force)
        if not ok:
            print(f"⛔ Un runner --go tourne déjà (PID {autre}) — un seul à la fois, sinon les "
                  f"positions s'empilent sur le compte démo.\n"
                  f"   Ferme l'autre fenêtre (Ctrl-C) puis relance ; ou --force si le verrou est périmé.")
            return
        atexit.register(liberer_verrou)
    from bitget_trading import BitgetTrading, BitgetError
    cfg = CONFIGS[strat]
    journal = Journal(cfg["slug"], "go" if go else "shadow")
    client = None
    if go:
        client = BitgetTrading(demo=True)          # 🔒 démo
        for lbl, fn in (("mode position", lambda: client.set_position_mode(True)),
                        ("mode marge", lambda: client.set_margin_mode(SYMBOL_BITGET)),
                        ("levier", lambda: client.set_leverage(SYMBOL_BITGET, lever))):
            try:
                fn()
            except BitgetError as e:
                print(f"  ({lbl}: {e})")

    ref = {}   # accès au moteur depuis le callback

    def emit(evenement, ts, **c):
        moteur = ref["m"]
        # garde-fou dislocation : à l'entrée, on peut refuser (shadow ET go).
        if evenement == "entree":
            try:
                disloque, info = basis_dislocation.evaluer(SYMBOL_BITGET, SYMBOL_BINANCE)
            except Exception as e:
                disloque, info = False, {"raison": f"éval KO: {e}", "net_bps": None}
            if disloque:
                journal.ecrire("refuse", ts, raison=f"dislocation ({info['raison']})",
                               net_bps=info.get("net_bps"))
                moteur.annuler_entree(ts)
                return
        journal.ecrire(evenement, ts, **c)
        if not go:
            return
        try:
            if evenement == "entree":
                side = "buy" if c["sens"] == "long" else "sell"
                client.place_market(SYMBOL_BITGET, side, size, sl=c["sl"], tp=c.get("tp"),
                                    client_oid=f"{strat}{int(time.time())}")
                ref["sl_id"] = None            # orderId du loss_plan, résolu à la 1re modif (H2)
            elif evenement == "stop_modifie" and strat == "h2":
                # stop suiveur H2 : on DÉPLACE le SL de position (loss_plan) CÔTÉ SERVEUR
                # (modify-tpsl-order sur son orderId — cf. bitget_trading.modify_position_sl).
                if ref.get("sl_id") is None:
                    ref["sl_id"] = client.plan_sl_orderid(SYMBOL_BITGET)
                if ref.get("sl_id"):
                    client.modify_position_sl(SYMBOL_BITGET, ref["sl_id"], c["prix"], size)
                else:
                    journal.ecrire("avert", ts, raison="loss_plan serveur introuvable — trailing sauté")
            elif evenement == "sortie" and c.get("code") != "SLTP":
                # croisement inverse (H2/H3) : le serveur ne l'a pas fait, on ferme au marché.
                client.close_all(SYMBOL_BITGET)
        except BitgetError as e:
            if e.code == "22002":              # « No position to close » : le SL/TP serveur a déjà
                journal.ecrire("info", ts, raison="déjà flat — SL/TP serveur a fermé avant le croisement")
            else:
                journal.ecrire("erreur", ts, raison=str(e))
            if evenement == "entree":
                moteur.annuler_entree(ts)
            elif evenement == "stop_modifie":
                ref["sl_id"] = None            # re-résoudre l'orderId au prochain coup

    # En --go, les 3 stratégies laissent le SERVEUR gérer la touche SL/TP (le moteur ne la simule
    # pas → fermeture_externe la constate). H2 : le moteur calcule le stop suiveur et le runner
    # DÉPLACE le SL serveur (loss_plan) à chaque barre. En shadow : le moteur simule tout.
    moteur = MoteurHybride(strat, rapide, lente, emit=emit, gerer_sl_tp=not go)
    ref["m"] = moteur

    print(f"Runner {strat.upper()} {cfg['nom']} — SMA {rapide}/{lente} 1m — Binance → "
          f"{'ORDRES démo Bitget' if go else 'SHADOW (aucun ordre)'}. Ctrl-C = flat + stop.\n")
    journal.ecrire("demarrage", int(time.time() * 1000),
                   raison=f"{cfg['nom']} SMA {rapide}/{lente}, {'GO' if go else 'SHADOW'}")

    # amorçage : chauffer SMA + ATR sur l'historique fermé, SANS trader (amorce=True). Sinon l'ATR
    # (14 barres) et la SMA lente ne sont prêts qu'après ~15 min de temps réel → les croisements du
    # début sont ratés. On garde la dernière barre fermée pour la traiter en live juste après.
    hist = klines_binance()
    for k in hist[:-1]:
        moteur.barre(int(k[0]) + 60_000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), amorce=True)
    dernier = hist[-2][6] if len(hist) >= 2 else None
    d = moteur.dernier
    print(f"  amorçage : {max(len(hist) - 1, 0)} barres chauffées — "
          f"ATR={d and d['atr'] and round(d['atr'], 1)}, prêt à détecter les croisements.\n")

    def traiter(ks):
        """Traite toutes les barres fermées postérieures à la dernière vue (en pratique 0 ou 1) :
        évite le trou possible à la frontière de minute. Renvoie le nombre de barres traitées
        (0 = la barre fermée n'est pas encore servie par Binance)."""
        nonlocal dernier
        n = 0
        for k in [b for b in ks if dernier is None or b[6] > dernier]:
            dernier = k[6]
            ts_barre = int(k[0]) + 60_000
            # --go : le SL/TP serveur a-t-il refermé la position depuis la dernière barre ? On
            # resync le moteur AVANT sa logique — sinon il traillerait/fermerait une position
            # fantôme (la sortie au croisement tenterait un close_all déjà fait -> 22002). Le
            # cooldown post-sortie empêche une réentrée sur la barre même.
            if go and moteur.pos != 0 and not _position_ouverte(client):
                moteur.fermeture_externe(ts_barre)
            moteur.barre(ts_barre, float(k[1]), float(k[2]), float(k[3]), float(k[4]))
            d = moteur.dernier
            if d:
                etat = ("EN POSITION" if moteur.pos else "plat")
                proche = "  ← proche d'un croisement !" if d["atr"] and abs(d["diff"]) < 0.3 * d["atr"] else ""
                # heure = clôture de la BARRE (locale), pas l'heure murale du traitement : la ligne
                # s'aligne ainsi sur la chandelle du graphe (avant : now() en UTC → ~4 s de décalage
                # apparent, l'illusion de « délai »).
                print(f"  · {datetime.fromtimestamp(ts_barre / 1000):%H:%M:%S} close={d['close']:.1f} "
                      f"SMA{rapide}={d['sr']:.1f} SMA{lente}={d['sl']:.1f} écart={d['diff']:+.1f} "
                      f"ATR={d['atr'] and round(d['atr'],1)} | {etat}{proche}")
            n += 1
        return n

    traiter(klines_binance())   # la dernière barre fermée depuis l'amorçage, traitée en direct
    while True:
        try:
            # se caler sur la clôture de minute : réagir DÈS le close (au lieu d'un cycle fixe de
            # 10 s qui décalait le stop suiveur de 10-15 s après la chandelle).
            time.sleep(secondes_avant_minute())
            # la barre fermée peut n'être servie que quelques centaines de ms après :00 → on
            # réessaie brièvement plutôt que de la rater et d'attendre la minute suivante.
            for _ in range(RETRIES):
                if traiter(klines_binance()):
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n⏹  Ctrl-C — kill switch.")
            if go and moteur.pos != 0:
                try:
                    client.close_all(SYMBOL_BITGET)
                except Exception as e:
                    print(f"  (close_all: {e})")
            journal.ecrire("arret", int(time.time() * 1000), raison="Ctrl-C")
            return
        except Exception as e:
            print(f"  ⚠ {type(e).__name__}: {e} — on réessaie dans 10 s")
            time.sleep(10)


def main():
    ap = argparse.ArgumentParser(description="Runner live des 3 hybrides (Binance→Bitget démo).")
    ap.add_argument("--strategie", choices=["h1", "h2", "h3"], default="h1")
    ap.add_argument("--go", action="store_true", help="exécuter sur la démo (sinon SHADOW)")
    ap.add_argument("--size", type=float, default=0.0001, help="taille en BTC (défaut 0,0001)")
    ap.add_argument("--lever", type=int, default=5)
    ap.add_argument("--rapide", type=int, default=SMA_RAPIDE, help=f"SMA rapide (défaut {SMA_RAPIDE})")
    ap.add_argument("--lente", type=int, default=SMA_LENTE, help=f"SMA lente (défaut {SMA_LENTE})")
    ap.add_argument("--force", action="store_true",
                    help="reprend un verrou périmé (un runner --go planté sans nettoyer)")
    a = ap.parse_args()
    lancer(a.strategie, a.go, a.size, a.lever, a.rapide, a.lente, a.force)


if __name__ == "__main__":
    main()
