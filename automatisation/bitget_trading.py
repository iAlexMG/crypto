# bitget_trading.py — ÉTAPE B du POC : client de trading Bitget USDT-M perp (mix v2), SIGNÉ.
#
# Fondé sur docs/etude-api-trading-bitget.md (checklist §4). Triplet démo FIGÉ par la sonde
# (2026-07-22) : productType USDT-FUTURES / marginCoin USDT / symbole BTCUSDT, en-tête
# `paptrading: 1`. Le « simulateur S… » du site n'est PAS la démo API.
#
# 🔒 GARDE DÉMO CODÉE EN DUR : par défaut le client tourne en DÉMO (paptrading:1). Il REFUSE
#    le compte RÉEL sauf `allow_real=True` explicite (2e barrière, comme le garde anti-compte-
#    réel des indices). Le secret et la passphrase ne sont jamais journalisés.
#
# Contient auth + config (levier, mode) + ordres (market + SL/TP attachés, close, cancel) +
# lecture (compte, positions, specs). Le runner (étape C) s'appuie dessus.
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.bitget.com"
CREDS = Path(__file__).resolve().parent / "credentials.local.json"


class BitgetError(RuntimeError):
    """Erreur métier Bitget (code != 00000)."""
    def __init__(self, code, msg):
        super().__init__(f"bitget {code}: {msg}")
        self.code, self.msg = code, msg


class BitgetTrading:
    def __init__(self, demo: bool = True, allow_real: bool = False,
                 product: str = "USDT-FUTURES", margin_coin: str = "USDT",
                 margin_mode: str = "isolated"):   # aligné sur l'UI démo (marge « Isolée »)
        # 🔒 garde démo : le réel exige un consentement EXPLICITE.
        if not demo and not allow_real:
            raise RuntimeError("REFUS : client en mode RÉEL sans allow_real=True. "
                               "Le POC vit en DÉMO (paptrading). N'active le réel qu'en connaissance.")
        self.demo = demo
        self.product = product
        self.margin_coin = margin_coin
        self.margin_mode = margin_mode
        self._api_key, self._secret, self._passphrase = self._charger()
        self._specs: dict[str, dict] = {}

    # ---- auth / transport -------------------------------------------------
    @staticmethod
    def _charger():
        if not CREDS.exists():
            raise RuntimeError(f"{CREDS.name} introuvable (copier credentials.local.example.json).")
        c = json.loads(CREDS.read_text(encoding="utf-8"))
        for k in ("apiKey", "secret", "passphrase"):
            if not c.get(k):
                raise RuntimeError(f"champ « {k} » vide dans {CREDS.name}.")
        return c["apiKey"], c["secret"], c["passphrase"]

    def _sign(self, ts, method, path_qs, body):
        prehash = f"{ts}{method.upper()}{path_qs}{body}"
        mac = hmac.new(self._secret.encode(), prehash.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    def _request(self, method, path, params=None, body=None, private=True):
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json", "locale": "en-US"}
        if self.demo:
            headers["paptrading"] = "1"
        if private:
            ts = str(int(time.time() * 1000))
            headers.update({
                "ACCESS-KEY": self._api_key,
                "ACCESS-SIGN": self._sign(ts, method, path + qs, body_str),
                "ACCESS-TIMESTAMP": ts,
                "ACCESS-PASSPHRASE": self._passphrase,
            })
        data = body_str.encode() if body_str else None
        req = urllib.request.Request(f"{BASE}{path}{qs}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                rep = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                rep = json.load(e)
            except Exception:
                raise BitgetError(str(e.code), e.reason)
        if rep.get("code") != "00000":
            raise BitgetError(rep.get("code"), rep.get("msg"))
        return rep.get("data")

    # ---- lecture ----------------------------------------------------------
    def account(self):
        """Comptes du productType (marginCoin, available, accountEquity…)."""
        return self._request("GET", "/api/v2/mix/account/accounts",
                             {"productType": self.product})

    def positions(self, symbol: str | None = None):
        p = {"productType": self.product, "marginCoin": self.margin_coin}
        if symbol:
            p["symbol"] = symbol
        return self._request("GET", "/api/v2/mix/position/all-position", p)

    def ticker(self, symbol: str) -> dict:
        """Ticker (public) : lastPr, markPrice, bidPr/askPr — pour ancrer SL/TP."""
        data = self._request("GET", "/api/v2/mix/market/ticker",
                             {"productType": self.product, "symbol": symbol}, private=False)
        return data[0] if data else {}

    def specs(self, symbol: str) -> dict:
        """Specs du contrat (public, marché réel), mises en cache."""
        if symbol not in self._specs:
            data = self._request("GET", "/api/v2/mix/market/contracts",
                                 {"productType": self.product, "symbol": symbol}, private=False)
            if not data:
                raise BitgetError("40034", f"{symbol} sans specs")
            self._specs[symbol] = data[0]
        return self._specs[symbol]

    # ---- arrondis (tick prix / pas de taille) -----------------------------
    def round_price(self, symbol, price):
        s = self.specs(symbol)
        tick = float(s["priceEndStep"]) * (10 ** -int(s["pricePlace"]))
        return round(round(price / tick) * tick, int(s["pricePlace"]))

    def round_size(self, symbol, size):
        s = self.specs(symbol)
        place = int(s["volumePlace"])
        mn = float(s["minTradeNum"])
        return max(mn, round(round(size, place), place))

    # ---- configuration ----------------------------------------------------
    def set_position_mode(self, one_way: bool = True):
        return self._request("POST", "/api/v2/mix/account/set-position-mode",
                             body={"productType": self.product,
                                   "posMode": "one_way_mode" if one_way else "hedge_mode"})

    def set_margin_mode(self, symbol, margin_mode: str | None = None):
        """Mode de marge du symbole : 'isolated' (défaut du client) ou 'crossed'."""
        return self._request("POST", "/api/v2/mix/account/set-margin-mode",
                             body={"symbol": symbol, "productType": self.product,
                                   "marginCoin": self.margin_coin,
                                   "marginMode": margin_mode or self.margin_mode})

    def set_leverage(self, symbol, leverage, hold_side: str | None = None):
        body = {"symbol": symbol, "productType": self.product,
                "marginCoin": self.margin_coin, "leverage": str(leverage)}
        if hold_side:
            body["holdSide"] = hold_side
        return self._request("POST", "/api/v2/mix/account/set-leverage", body=body)

    # ---- ordres -----------------------------------------------------------
    def place_market(self, symbol, side, size, sl=None, tp=None,
                     client_oid=None, reduce_only=False):
        """Ordre MARKET one-way (side='buy'/'sell'), avec SL/TP ATTACHÉS (preset).
        `size` en coin de base (BTC) ; SL/TP en prix (arrondis au tick). Renvoie la data
        (orderId, clientOid). Le bracket est le pendant crypto du H1 des indices."""
        body = {
            "symbol": symbol, "productType": self.product, "marginMode": self.margin_mode,
            "marginCoin": self.margin_coin, "size": str(self.round_size(symbol, size)),
            "side": side, "orderType": "market",
        }
        if reduce_only:
            body["reduceOnly"] = "YES"
        if sl is not None:
            body["presetStopLossPrice"] = str(self.round_price(symbol, sl))
        if tp is not None:
            body["presetStopSurplusPrice"] = str(self.round_price(symbol, tp))
        if client_oid:
            body["clientOid"] = client_oid
        return self._request("POST", "/api/v2/mix/order/place-order", body=body)

    def plan_sl_orderid(self, symbol):
        """orderId du plan STOP-LOSS de position (planType 'loss_plan') en attente — celui que
        crée `presetStopLossPrice` à l'entrée. None s'il n'y en a pas. Sert le stop suiveur H2 :
        on récupère l'orderId, puis on le DÉPLACE via modify_position_sl (l'orderId est stable
        d'une modif à l'autre — mesuré sur la démo)."""
        d = self._request("GET", "/api/v2/mix/order/orders-plan-pending",
                         {"productType": self.product, "planType": "profit_loss", "symbol": symbol})
        lst = ((d or {}).get("entrustedList") if isinstance(d, dict) else d) or []
        for o in lst:
            if o.get("planType") == "loss_plan":
                return o.get("orderId")
        return None

    def modify_position_sl(self, symbol, order_id, trigger_price, size, trigger_type="mark_price"):
        """DÉPLACE le stop-loss de position (loss_plan) CÔTÉ SERVEUR = le vrai stop suiveur H2.
        En one-way (position NETTE), place-tpsl-order/pos_loss échoue en 43011 (pas de holdSide
        sur une position nette, mesuré) → on MODIFIE le plan existant par son orderId. Bitget
        EXIGE `size` et `executePrice` ici (0 = exécution au marché) — sinon 400172 « Order
        quantity cannot be empty »."""
        return self._request("POST", "/api/v2/mix/order/modify-tpsl-order",
                             body={"orderId": str(order_id), "marginCoin": self.margin_coin,
                                   "productType": self.product, "symbol": symbol,
                                   "triggerType": trigger_type,
                                   "triggerPrice": str(self.round_price(symbol, trigger_price)),
                                   "size": str(self.round_size(symbol, size)), "executePrice": "0"})

    def close_all(self, symbol: str | None = None, hold_side: str | None = None):
        """Flat / kill switch : ferme la/les position(s) au marché."""
        body = {"productType": self.product}
        if symbol:
            body["symbol"] = symbol
        if hold_side:
            body["holdSide"] = hold_side
        return self._request("POST", "/api/v2/mix/order/close-positions", body=body)

    def cancel_all(self, symbol: str | None = None):
        body = {"productType": self.product, "marginCoin": self.margin_coin}
        if symbol:
            body["symbol"] = symbol
        return self._request("POST", "/api/v2/mix/order/cancel-all-orders", body=body)

    def order_detail(self, symbol, order_id=None, client_oid=None):
        p = {"symbol": symbol, "productType": self.product}
        if order_id:
            p["orderId"] = str(order_id)
        if client_oid:
            p["clientOid"] = client_oid
        return self._request("GET", "/api/v2/mix/order/detail", p)
