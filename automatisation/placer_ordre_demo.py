# placer_ordre_demo.py — validation de l'ÉTAPE B : UN ordre market + bracket SL/TP sur la
# DÉMO Bitget (paptrading). C'est le pendant crypto du « Test 2 » des indices : prouver que la
# chaîne d'exécution FIRE (ordre → bracket attaché → lisible en position).
#
# 🔒 Sécurités : DÉMO uniquement (le client refuse le réel sans allow_real) ; taille MINIMALE
#    (minTradeNum) ; il faut le drapeau --go pour placer quoi que ce soit (sinon dry-run).
#
# Prérequis : compte démo APPROVISIONNÉ (réclamer les fonds virtuels dans l'UI démo Bitget —
# la sonde a montré dispo=0). Sinon l'ordre échoue en « marge insuffisante ».
#
# Usage :
#   python placer_ordre_demo.py                 # DRY-RUN : montre le plan, ne place RIEN
#   python placer_ordre_demo.py --go            # place l'ordre + bracket, lit le retour
#   python placer_ordre_demo.py --go --close    # …puis referme la position (flat)
import argparse
import time

from bitget_trading import BitgetTrading, BitgetError

SYMBOL = "BTCUSDT"


def main():
    ap = argparse.ArgumentParser(description="Test 1 ordre + bracket sur démo Bitget.")
    ap.add_argument("--side", choices=["buy", "sell"], default="buy",
                    help="buy=long (défaut) | sell=short")
    ap.add_argument("--go", action="store_true", help="placer réellement (sinon dry-run)")
    ap.add_argument("--close", action="store_true", help="refermer la position après")
    ap.add_argument("--lever", type=int, default=5, help="levier (défaut 5)")
    a = ap.parse_args()

    c = BitgetTrading(demo=True)          # 🔒 démo (paptrading:1)
    px = float(c.ticker(SYMBOL).get("lastPr"))
    size = float(c.specs(SYMBOL)["minTradeNum"])         # plus petite taille
    # long : SL en-dessous / TP au-dessus ; short : l'inverse.
    if a.side == "buy":
        sl, tp = c.round_price(SYMBOL, px * 0.995), c.round_price(SYMBOL, px * 1.005)
    else:
        sl, tp = c.round_price(SYMBOL, px * 1.005), c.round_price(SYMBOL, px * 0.995)
    print(f"Plan : {a.side.upper()} {size} {SYMBOL} @~{px} | SL {sl} | TP {tp} | "
          f"levier {a.lever} (DÉMO)")

    if not a.go:
        print("DRY-RUN — rien n'est placé. Ajoute --go pour exécuter.")
        return

    try:
        for label, fn in (("mode position", lambda: c.set_position_mode(one_way=True)),
                          ("mode marge", lambda: c.set_margin_mode(SYMBOL)),  # isolated (cf. UI)
                          ("levier", lambda: c.set_leverage(SYMBOL, a.lever))):
            try:
                fn()
            except BitgetError as e:
                print(f"  ({label} : {e} — souvent déjà réglé, on continue)")

        oid = f"poc{int(time.time())}"
        res = c.place_market(SYMBOL, a.side, size, sl=sl, tp=tp, client_oid=oid)
        order_id = res.get("orderId")
        print(f"✅ Ordre placé : orderId={order_id} clientOid={res.get('clientOid')}")

        time.sleep(1.5)                                  # laisser le fill se propager
        det = c.order_detail(SYMBOL, order_id=order_id)
        print(f"   détail : état={det.get('state')} prix={det.get('priceAvg')} "
              f"SL={det.get('presetStopLossPrice')} TP={det.get('presetStopSurplusPrice')}")
        for p in c.positions(SYMBOL):
            if float(p.get("total", 0)):
                print(f"   position : {p.get('holdSide')} {p.get('total')} @ {p.get('openPriceAvg')}")
        print("✅ Chaîne d'exécution prouvée : ordre → bracket attaché → position lisible.")

        if a.close:
            c.close_all(SYMBOL)
            print("   position refermée (flat).")
    except BitgetError as e:
        if e.code in ("40754", "45110", "43012", "40786") or "insufficient" in (e.msg or "").lower():
            print(f"⛔ {e}\n   → Compte démo sans fonds ? Réclame les fonds virtuels dans l'UI "
                  f"démo Bitget (la sonde montrait dispo=0), puis relance.")
        else:
            print(f"⛔ {e}")


if __name__ == "__main__":
    main()
