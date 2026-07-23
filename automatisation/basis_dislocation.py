# basis_dislocation.py — ÉTAPE C2 : le « pont hybride » du runner, en version légère (REST).
#
# Deux rôles, repris de l'affichage (docs/hybride.md §1 + gui/arbitrage.py find_arbs) :
#   1. BASIS = écart de prix Binance↔Bitget (mid Bitget − mid Binance). Un basis STABLE de
#      quelques $ est NORMAL (perp ≠ perp) ; on le journalise pour la transparence.
#   2. DISLOCATION = les carnets se CROISENT (best bid d'un exchange AU-DESSUS du best ask de
#      l'autre) d'un écart net des frais taker > seuil → signal d'anomalie (un flux en retard,
#      gelé, ou suspect). C'est le garde-fou : le runner REFUSE d'entrer quand ça disloque.
#
# Léger et pollable (top-of-book REST des deux côtés), stdlib seule — pas besoin des WS de
# l'affichage. Le basis n'ancre PAS le SL/TP ici (le runner lit déjà le prix Bitget réel pour
# ça) ; il sert à mesurer et à garder.
from __future__ import annotations

import json
import urllib.parse
import urllib.request

BINANCE_BOOK = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
BITGET_TICKER = "https://api.bitget.com/api/v2/mix/market/ticker"   # public (pas de clés)

# Frais taker (bps), palier standard — mêmes valeurs que gui/arbitrage.py (FUTURES).
TAKER_BPS = {"binance": 5.0, "bitget": 6.0}
SEUIL_DISLOC_BPS = 1.0     # écart net (après frais) qui signale une dislocation (cf. ARB_THRESHOLD_BPS)
SEUIL_BASIS_BPS = 30.0     # basis anormalement large (~feed gelé/suspect) → on garde aussi


def binance_book(symbol="BTCUSDT"):
    url = f"{BINANCE_BOOK}?" + urllib.parse.urlencode({"symbol": symbol})
    with urllib.request.urlopen(url, timeout=8) as r:
        d = json.load(r)
    return float(d["bidPrice"]), float(d["askPrice"])


def bitget_book(symbol="BTCUSDT"):
    url = f"{BITGET_TICKER}?" + urllib.parse.urlencode(
        {"productType": "USDT-FUTURES", "symbol": symbol})
    with urllib.request.urlopen(url, timeout=8) as r:
        d = json.load(r)
    t = (d.get("data") or [{}])[0]
    return float(t["bidPr"]), float(t["askPr"])


def evaluer(symbol_bitget="BTCUSDT", symbol_binance="BTCUSDT"):
    """Renvoie (disloque: bool, info: dict). Tout en PUBLIC (marche aussi en shadow)."""
    bg_bid, bg_ask = bitget_book(symbol_bitget)
    bn_bid, bn_ask = binance_book(symbol_binance)
    bg_mid, bn_mid = (bg_bid + bg_ask) / 2, (bn_bid + bn_ask) / 2
    basis = bg_mid - bn_mid
    fee = TAKER_BPS["binance"] + TAKER_BPS["bitget"]

    # carnets croisés (net des frais), dans les deux sens
    net = max((bn_bid - bg_ask) / bg_mid * 1e4 - fee,
              (bg_bid - bn_ask) / bn_mid * 1e4 - fee)
    basis_bps = abs(basis) / bn_mid * 1e4
    disloque = net >= SEUIL_DISLOC_BPS or basis_bps >= SEUIL_BASIS_BPS
    raison = ("carnets croisés" if net >= SEUIL_DISLOC_BPS
              else "basis anormal" if basis_bps >= SEUIL_BASIS_BPS else "")
    return disloque, {"basis": round(basis, 2), "basis_bps": round(basis_bps, 2),
                      "net_bps": round(net, 2), "raison": raison}
