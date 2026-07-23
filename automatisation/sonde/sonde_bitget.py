# sonde_bitget.py — ÉTAPE A du POC automatisation Bitget : sonde SIGNÉE, STRICTEMENT
# EN LECTURE, contre la DÉMO Bitget (paptrading). Aucune ligne ne place, ne modifie ni
# n'annule d'ordre — c'est délibéré (on prouve l'auth + le mode démo avant de bâtir le
# client de trading). Voir docs/etude-api-trading-bitget.md.
#
# Ce qu'elle mesure (« mesurer, pas supposer ») :
#   1. l'auth v2 (ACCESS-KEY/SIGN/TIMESTAMP/PASSPHRASE) + l'en-tête démo `paptrading:1`
#      fonctionnent → lecture du compte de démo (solde virtuel) ;
#   2. TRANCHE l'ambiguïté du productType démo : SUSDT-FUTURES (symboles préfixés S, ex.
#      SBTCSUSDT) VS USDT-FUTURES normal — teste les deux et dit lequel répond `00000` ;
#   3. specs du symbole (tick, pas de prix, taille mini, multiplicateur) → pour le sizing ;
#   4. positions ouvertes (baseline).
#
# Clés : credentials.local.json (gitignoré) à la racine de ../ (automatisation/). Le secret
# et la passphrase ne sont JAMAIS journalisés ni affichés.
#
# Usage :
#   python sonde_bitget.py                 # démo (défaut), symbole SBTCSUSDT
#   python sonde_bitget.py --symbol SETHSUSDT
#   python sonde_bitget.py --real          # ⚠️ omet paptrading -> compte RÉEL (lecture seule)
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.bitget.com"
CREDS = Path(__file__).resolve().parents[1] / "credentials.local.json"


def charger_cles():
    if not CREDS.exists():
        sys.exit(f"⛔ {CREDS.name} introuvable.\n"
                 f"   Copie credentials.local.example.json en credentials.local.json et colle "
                 f"tes clés de DÉMO Bitget (mode Démo -> Personal Center -> API Key Management).")
    c = json.loads(CREDS.read_text(encoding="utf-8"))
    for k in ("apiKey", "secret", "passphrase"):
        if not c.get(k):
            sys.exit(f"⛔ champ « {k} » vide dans {CREDS.name}.")
    return c["apiKey"], c["secret"], c["passphrase"]


def signer(secret, timestamp, method, chemin_complet, body=""):
    """ACCESS-SIGN = base64(HMAC-SHA256(secret, timestamp + METHOD + requestPath+query + body))."""
    prehash = f"{timestamp}{method.upper()}{chemin_complet}{body}"
    mac = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(mac).decode()


def _appel(method, path, params, cles, demo, prive):
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = f"{BASE}{path}{query}"
    headers = {"Content-Type": "application/json", "locale": "en-US"}
    if demo:
        headers["paptrading"] = "1"
    if prive:
        api_key, secret, passphrase = cles
        ts = str(int(time.time() * 1000))
        headers.update({
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": signer(secret, ts, method, path + query, ""),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": passphrase,
        })
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)          # Bitget renvoie {code,msg} même en 4xx
        except Exception:
            return {"code": str(e.code), "msg": e.reason}


def _ok(rep):
    return rep.get("code") == "00000"


def check(titre, method, path, params, cles, demo, prive=True):
    rep = _appel(method, path, params, cles, demo, prive)
    statut = "✅" if _ok(rep) else "⛔"
    print(f"{statut} {titre} — code={rep.get('code')} msg={rep.get('msg')}")
    return rep


def main():
    ap = argparse.ArgumentParser(description="Sonde read-only signée — démo Bitget (POC étape A).")
    ap.add_argument("--symbol", default=None,
                    help="forcer un symbole (sinon déduit du couple tradable détecté)")
    ap.add_argument("--real", action="store_true",
                    help="⚠️ omet paptrading -> compte RÉEL (lecture seule quand même)")
    a = ap.parse_args()
    demo = not a.real
    cles = charger_cles()
    print(f"=== Sonde Bitget ({'DÉMO paptrading:1' if demo else 'RÉEL'}) — lecture seule ===\n")

    # 1) OÙ VIT LE COMPTE DE DÉMO ? On lit les comptes des DEUX productType et on affiche
    #    les soldes : le compte virtuel (~50 000) révèle le produit réellement tradé en démo.
    print("--- comptes (les 2 productType) ---")
    trouve = []
    for pt in ("USDT-FUTURES", "SUSDT-FUTURES"):
        rep = check(f"accounts {pt}", "GET", "/api/v2/mix/account/accounts",
                    {"productType": pt}, cles, demo)
        for acc in (rep.get("data") or []):
            print(f"      {acc.get('marginCoin')}: dispo={acc.get('available')} "
                  f"equity={acc.get('accountEquity')}")
        if _ok(rep) and (rep.get("data") or []):
            trouve.append(pt)
    if not trouve:
        print("\n⛔ Aucun compte non vide. Clés de DÉMO ? passphrase ? horloge à l'heure ? "
              "IP autorisée ? (code 40099 = environnement/clé incorrects.)")
        return

    # 2) TRANCHER le couple (productType, marginCoin, symbole) réellement tradable en démo,
    #    en interrogeant les POSITIONS sur les combinaisons plausibles (mesuré : SUSDT-FUTURES
    #    refuse la marge SUSDT -> 40778 ; le simulateur S… du site n'est pas la démo API).
    print("\n--- couple tradable (positions : quelle combinaison répond ?) ---")
    combos = [("USDT-FUTURES", "USDT", "BTCUSDT"),
              ("SUSDT-FUTURES", "USDT", "BTCUSDT"),
              ("SUSDT-FUTURES", "SUSDT", "SBTCSUSDT")]
    product = margin = symbole = None
    for pt, mc, sym in combos:
        rep = check(f"all-position {pt} / marge {mc}", "GET",
                    "/api/v2/mix/position/all-position",
                    {"productType": pt, "marginCoin": mc}, cles, demo)
        if _ok(rep) and product is None:
            product, margin, symbole = pt, mc, sym
            for p in (rep.get("data") or []):
                print(f"      position: {p.get('symbol')} {p.get('holdSide')} "
                      f"taille={p.get('total')} pmoyen={p.get('openPriceAvg')}")
    if product is None:
        print("\n⚠️ Aucune combinaison de position n'a répondu 00000 — colle cette sortie, "
              "on ajuste (l'endpoint account marchait, donc l'auth est bonne).")
        return
    if a.symbol:
        symbole = a.symbol
    print(f"\n→ DÉMO TRADABLE : productType={product}  marginCoin={margin}  symbole={symbole}")

    # 3) specs du symbole retenu (marché RÉEL, public — la démo exécute sur ces prix).
    print("\n--- specs symbole (public) ---")
    specs = _appel("GET", "/api/v2/mix/market/contracts",
                   {"productType": product, "symbol": symbole}, cles, demo=False, prive=False)
    if _ok(specs) and specs.get("data"):
        d = specs["data"][0]
        print(f"✅ {symbole}: pricePlace={d.get('pricePlace')} priceEndStep={d.get('priceEndStep')} "
              f"minTradeNum={d.get('minTradeNum')} minTradeUSDT={d.get('minTradeUSDT')} "
              f"maxLever={d.get('maxLever')} tick(calc)={_tick(d)}")
    else:
        print(f"⛔ specs indisponibles pour {symbole} — code={specs.get('code')} msg={specs.get('msg')}")

    print("\n✅ Sonde terminée. Rien n'a été placé/modifié/annulé (lecture seule).")


def _tick(d):
    """Tick de prix = 10^-pricePlace × priceEndStep (convention Bitget mix)."""
    try:
        return float(d["priceEndStep"]) * (10 ** -int(d["pricePlace"]))
    except Exception:
        return "?"


if __name__ == "__main__":
    main()
