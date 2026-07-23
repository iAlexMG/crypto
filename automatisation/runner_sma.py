# runner_sma.py — ÉTAPE C du POC : le RUNNER. Signal SMA cross sur données BINANCE →
# exécution sur BITGET démo (ordre market + bracket SL/TP). Pendant crypto du H1 des indices
# (SMA 9/21, bracket SL 1,5×ATR / TP 1R). Objectif = prouver la CHAÎNE (pas la rentabilité).
#
# Deux modes (comme les indices) :
#   défaut = SHADOW : calcule les croisements + journalise, N'ENVOIE AUCUN ORDRE ;
#   --go            = exécute sur la DÉMO Bitget (paptrading) via bitget_trading.
#
# Garde-fous : DÉMO uniquement (le client refuse le réel) ; taille bornée ; cooldown ;
# kill switch (Ctrl-C → flat + journal). Le pont basis / garde-fou dislocation viendront
# en C2 (ici on prouve d'abord le squelette signal→ordre→bracket→gestion).
#
# Usage :
#   python runner_sma.py                 # SHADOW (aucun ordre), journalise les signaux
#   python runner_sma.py --go            # exécute sur la démo Bitget
#   python runner_sma.py --go --size 0.0002
from __future__ import annotations

import argparse
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import basis_dislocation

BINANCE = "https://fapi.binance.com/fapi/v1/klines"   # USDT-M perp, données de RÉFÉRENCE
SYMBOL_BINANCE = "BTCUSDT"
SYMBOL_BITGET = "BTCUSDT"
SMA_RAPIDE, SMA_LENTE, ATR_N = 3, 9, 14   # 3/9 = croisements plus fréquents (démo « pour voir »)
STOP_MULT, TP_R = 1.5, 1.0          # SL = 1,5×ATR (=R) ; TP = 1R  (identiques au H1 indices)
COOLDOWN_S = 120
JOURNAL_DIR = Path(__file__).resolve().parent / "journaux" / "sma_bitget"


def klines_binance(limit=60):
    """Bougies 1 m CLÔTURÉES (on retire la bougie en cours = dernière)."""
    url = f"{BINANCE}?" + urllib.parse.urlencode(
        {"symbol": SYMBOL_BINANCE, "interval": "1m", "limit": limit})
    with urllib.request.urlopen(url, timeout=10) as r:
        rows = json.load(r)
    return rows[:-1]     # la dernière est en cours de formation


def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def atr(highs, lows, closes, n):
    if len(closes) < n + 1:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    return sum(trs[-n:]) / n


class Journal:
    def __init__(self, mode):
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self._f = None
        self._jour = None

    def ecrire(self, evenement, **champs):
        t = datetime.now(timezone.utc)
        jour = t.date()
        if jour != self._jour:
            if self._f:
                self._f.close()
            self._jour = jour
            self._f = open(JOURNAL_DIR / f"{jour:%Y-%m-%d}.ndjson", "a", encoding="utf-8")
        rec = {"ts": t.isoformat(), "strategie": "sma_bitget", "symbole": SYMBOL_BITGET,
               "mode": self.mode, "evenement": evenement, **champs}
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._f.flush()
        detail = " ".join(f"{k}={v}" for k, v in champs.items()
                          if k in ("raison", "prix", "sens", "sl", "tp"))
        print(f"  [{t:%H:%M:%S}] {evenement} {detail}")


def boucle(client, journal, size, go):
    dernier_close = None
    diff_prec = None
    en_position = False
    cooldown_jusqu = 0.0
    print(f"Runner SMA {SMA_RAPIDE}/{SMA_LENTE} 1m — signal Binance → "
          f"{'ORDRES démo Bitget' if go else 'SHADOW (aucun ordre)'}. Ctrl-C = flat + stop.\n")
    journal.ecrire("demarrage", raison=f"SMA {SMA_RAPIDE}/{SMA_LENTE}, {'GO' if go else 'SHADOW'}")

    while True:
        try:
            ks = klines_binance()
            closes = [float(k[4]) for k in ks]
            highs = [float(k[2]) for k in ks]
            lows = [float(k[3]) for k in ks]
            close_time = ks[-1][6]

            # nouvelle bougie clôturée ?
            if close_time != dernier_close and len(closes) >= SMA_LENTE + 1:
                dernier_close = close_time
                sr, sl_ = sma(closes, SMA_RAPIDE), sma(closes, SMA_LENTE)
                diff = sr - sl_
                a = atr(highs, lows, closes, ATR_N)
                crois = 0
                if diff_prec is not None:
                    if diff_prec <= 0 < diff:
                        crois = 1
                    elif diff_prec >= 0 > diff:
                        crois = -1
                diff_prec = diff

                # battement de cœur : l'état à chaque bougie (pour la regarder vivre).
                etat = "EN POSITION" if en_position else "plat"
                cote = "long" if diff >= 0 else "short"
                proche = "  ← proche d'un croisement !" if a and abs(diff) < 0.3 * a else ""
                print(f"  · {datetime.now(timezone.utc):%H:%M:%S}Z close={closes[-1]:.1f} "
                      f"SMA{SMA_RAPIDE}={sr:.1f} SMA{SMA_LENTE}={sl_:.1f} écart={diff:+.1f} ({cote}) "
                      f"ATR={a:.1f} | {etat}{proche}")

                # si en position : le bracket gère (H1 ignore les croisements). On détecte le flat.
                if en_position and go:
                    if not any(float(p.get("total", 0)) for p in client.positions(SYMBOL_BITGET)):
                        en_position = False
                        cooldown_jusqu = time.time() + COOLDOWN_S
                        journal.ecrire("sortie", raison="bracket refermé (SL/TP touché)")

                if crois and not en_position and time.time() >= cooldown_jusqu and a:
                    sens = "long" if crois > 0 else "short"
                    try:
                        disloque, info = basis_dislocation.evaluer(SYMBOL_BITGET, SYMBOL_BINANCE)
                    except Exception as e:          # fail-open : un hoquet réseau ne masque pas le signal
                        disloque, info = False, {"basis": None, "net_bps": None,
                                                 "basis_bps": None, "raison": f"éval KO: {e}"}
                    # niveaux qu'on POSERAIT (prix Bitget public → marche aussi en shadow).
                    side, px, sl, tp = _niveaux(sens, a, closes[-1])
                    journal.ecrire("signal", prix=round(closes[-1], 1), sens=sens,
                                   raison=f"croisement {'haussier' if crois > 0 else 'baissier'} -> {sens}",
                                   sma_rapide=round(sr, 1), sma_lente=round(sl_, 1), atr=round(a, 1),
                                   basis=info["basis"])
                    if disloque:                    # garde-fou : carnets croisés / basis anormal
                        journal.ecrire("refuse", raison=f"dislocation ({info['raison']})",
                                       net_bps=info["net_bps"], basis_bps=info["basis_bps"])
                        cooldown_jusqu = time.time() + COOLDOWN_S
                    elif go:
                        _entrer(client, journal, side, sens, size, px, sl, tp)
                        en_position = True
                    else:                           # shadow : ordre SIMULÉ + bracket (dry-run fidèle)
                        journal.ecrire("entree_shadow", prix=round(px, 1), sens=sens,
                                       sl=round(sl, 1), tp=round(tp, 1),
                                       raison=f"ordre simulé {side} + bracket (aucun ordre réel)")
                        cooldown_jusqu = time.time() + COOLDOWN_S

            time.sleep(10)
        except KeyboardInterrupt:
            print("\n⏹  Ctrl-C — kill switch.")
            if go and en_position:
                try:
                    client.close_all(SYMBOL_BITGET)
                    journal.ecrire("kill", raison="Ctrl-C → flat")
                except Exception as e:
                    print(f"  (close_all: {e})")
            journal.ecrire("arret", raison="Ctrl-C")
            return
        except Exception as e:
            print(f"  ⚠ {type(e).__name__}: {e} — on réessaie dans 10 s")
            time.sleep(10)


def _niveaux(sens, atr, close_repli):
    """Prix d'entrée (mid Bitget public) + SL/TP (H1 : SL 1,5×ATR, TP 1R). Marche en shadow."""
    try:
        bid, ask = basis_dislocation.bitget_book(SYMBOL_BITGET)
        px = (bid + ask) / 2
    except Exception:
        px = close_repli                       # repli : dernier close Binance
    r = STOP_MULT * atr
    if sens == "long":
        return "buy", px, px - r, px + TP_R * r
    return "sell", px, px + r, px - TP_R * r


def _entrer(client, journal, side, sens, size, px, sl, tp):
    from bitget_trading import BitgetError
    try:
        res = client.place_market(SYMBOL_BITGET, side, size, sl=sl, tp=tp,
                                  client_oid=f"sma{int(time.time())}")
        journal.ecrire("entree", prix=round(px, 1), sens=sens, sl=round(sl, 1), tp=round(tp, 1),
                       raison=f"market {side} + bracket", order_id=res.get("orderId"))
    except BitgetError as e:
        journal.ecrire("erreur", raison=str(e))
        print(f"  ⛔ ordre refusé : {e}")


def main():
    ap = argparse.ArgumentParser(description="Runner SMA cross Binance→Bitget démo (POC).")
    ap.add_argument("--go", action="store_true", help="exécuter sur la démo (sinon SHADOW)")
    ap.add_argument("--size", type=float, default=0.0001, help="taille en BTC (défaut 0,0001)")
    ap.add_argument("--lever", type=int, default=5, help="levier (défaut 5)")
    a = ap.parse_args()

    client = None
    if a.go:
        from bitget_trading import BitgetTrading, BitgetError
        client = BitgetTrading(demo=True)      # 🔒 démo
        for label, fn in (("mode position", lambda: client.set_position_mode(True)),
                          ("mode marge", lambda: client.set_margin_mode(SYMBOL_BITGET)),
                          ("levier", lambda: client.set_leverage(SYMBOL_BITGET, a.lever))):
            try:
                fn()
            except BitgetError as e:
                print(f"  ({label}: {e})")

    journal = Journal("go" if a.go else "shadow")
    boucle(client, journal, a.size, a.go)


if __name__ == "__main__":
    main()
