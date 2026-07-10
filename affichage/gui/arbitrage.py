"""Detection de DISLOCATION de carnet inter-exchanges (Phase 3, cf. docs/hybride.md §3.3).

⚠ Ce n'est PAS un arbitrage executable sans risque, mais un signal de DISLOCATION du
carnet : le best bid d'une venue passe AU-DESSUS du best ask d'une autre, d'un ecart qui
depasse les frais taker des deux cotes. On NE tient PAS compte du funding/basis des perps
(deux perps differents ne convergent pas instantanement -> tenir le spread accumule du
funding), de la latence et du capital a immobiliser sur deux venues, ni du slippage au-dela
du top-of-book. L'etiquette "$/u" est donc INDICATIVE, pas une garantie d'execution.

On filtre par NOTIONAL minimal (quantite EXECUTABLE au top-of-book x mid) : sans ce filtre,
un croisement de 0,001 BTC declenchait le meme signal qu'un vrai (cf. revue #5). La quantite
realisable est bornee par le plus petit des deux cotes (min(taille best bid, taille best ask)).

Pour une paire (X, Y) : vendre au best bid de X et acheter au best ask de Y rapporte
`best_bid(X) - best_ask(Y)` par unite, net des frais TAKER des deux cotes. L'ecart net est
exprime en bps (compose directement avec les frais en %) ; on expose aussi l'equivalent $/u,
la quantite executable et le notional.
"""
from __future__ import annotations

from dataclasses import dataclass

# Frais TAKER par defaut, en bps (1 bps = 0.01 %). Palier STANDARD (VIP 0), sans reduction
# de token. Sources publiques 2026 (Bitget/Binance/Bybit/OKX/KuCoin). Table EDITABLE (UI
# Phase 3b) : ces valeurs ne sont qu'un defaut conservateur (taker plein des deux cotes).
TAKER_FEES_BPS: dict[str, dict[str, float]] = {
    "Bitget":  {"FUTURES": 6.0, "SPOT": 10.0},
    "Binance": {"FUTURES": 5.0, "SPOT": 10.0},
    "Bybit":   {"FUTURES": 5.5, "SPOT": 10.0},
    "OKX":     {"FUTURES": 5.0, "SPOT": 10.0},
    "KuCoin":  {"FUTURES": 6.0, "SPOT": 10.0},
}
ARB_THRESHOLD_BPS = 1.0      # ecart net MINIMUM (apres frais) pour signaler une dislocation
# Notional minimal (en QUOTE, ~USDT) de la quantite executable au top-of-book. En dessous,
# le croisement est du bruit (taille derisoire) -> ignore. EDITABLE (UI Phase 3b).
MIN_NOTIONAL_USD = 1000.0


@dataclass
class Arb:
    sell_ex: str      # on VEND au best bid de cette venue
    buy_ex: str       # on ACHETE au best ask de cette venue
    edge: float       # best_bid(sell) - best_ask(buy), en $ (prix bruts)
    net_bps: float    # ecart apres frais taker des deux cotes, en bps
    net_usd: float    # ecart par unite (1 BTC) apres frais, en $
    ts: int           # ms
    mid: float = 0.0  # (best_bid_sell + best_ask_buy)/2 -> position du marqueur graphe
    qty: float = 0.0       # quantite EXECUTABLE au top-of-book = min(taille bid, taille ask)
    notional: float = 0.0  # qty * mid (~USDT) -> sert le filtre de taille


def find_arbs(books: dict, fees_bps: dict, threshold_bps: float, ts: int,
              min_notional: float = 0.0) -> list[Arb]:
    """Dislocations de carnet >= seuil, triees par ecart net decroissant.

    `books` = {label_exchange: OrderBook} ; `fees_bps` = {label_exchange: taker_bps}.
    On teste TOUTES les paires ordonnees (vendre sur X, acheter sur Y) sur PRIX BRUTS.
    net_bps = edge/mid*1e4 - fee_X - fee_Y. Un edge <= 0 (pas de croisement) n'en est jamais
    une. On ignore aussi les croisements dont le NOTIONAL executable (min des deux tailles
    top-of-book x mid) est sous `min_notional` -> pas de signal sur une taille derisoire."""
    items = [(ex, b) for ex, b in books.items()
             if b is not None and b.bids and b.asks]
    out: list[Arb] = []
    for sell_ex, bsell in items:
        bid, bid_sz = bsell.bids[0]
        for buy_ex, bbuy in items:
            if buy_ex == sell_ex:
                continue
            ask, ask_sz = bbuy.asks[0]
            edge = bid - ask
            if edge <= 0:
                continue
            mid = (bid + ask) / 2.0
            fee = fees_bps.get(sell_ex, 0.0) + fees_bps.get(buy_ex, 0.0)
            net_bps = edge / mid * 1e4 - fee
            if net_bps < threshold_bps:
                continue
            qty = min(bid_sz, ask_sz)          # on ne traverse que le plus petit des deux cotes
            notional = qty * mid
            if notional < min_notional:        # croisement trop petit -> bruit, on ignore
                continue
            out.append(Arb(sell_ex, buy_ex, edge, net_bps,
                           mid * net_bps / 1e4, ts, mid, qty, notional))
    out.sort(key=lambda a: a.net_bps, reverse=True)
    return out
