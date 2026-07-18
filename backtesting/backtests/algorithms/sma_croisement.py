# Croisement de moyennes mobiles — SCALPING 1 m, multi-TF, long/short.
# PATRON de la refonte scalping. RE-CALIBRAGE anti-frais (2026-07-17) : la v1 (signal
# 1 m) sur-tradait (2 804 ordres, 65 k$ de frais). Leviers appliqués ici :
#   - SIGNAL sur barres 15 m (le croisement se lit aux bornes de 15 min, ~15x moins
#     d'événements qu'en 1 m) ; exécution/stop restent en 1 m.
#   - RÉGIME de fond 15 m (SMA 48 ≈ 12 h) : on n'entre que dans le sens de la tendance.
#   - COOLDOWN de 60 min après chaque sortie (bride la fréquence de ré-entrée).
#   - STOP élargi à 1,5 % (les trades respirent -> moins de stops -> moins de churn).
#   - Sortie : croisement inverse OU stop. Long ET short -> plus comparable au Buy & Hold.
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Même source de vérité que donnees.py / buyhold.py (OHLCV 1 m)
DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004      # 0,04 % Binance USDⓈ-M — LA constante de frais de la formation
CAPITAL = 100_000

# ── Paramètres scalping (choisis A PRIORI — leçon 07 : jamais après la courbe)
TF_SIGNAL = 15           # cadence du signal (minutes) : croisement lu sur barres 15 m
SMA_RAPIDE = 9           # SMA courte, barres 15 m (≈ 2,25 h)
SMA_LENTE = 21           # SMA longue, barres 15 m (≈ 5,25 h)
REGIME_N = 48            # SMA de régime sur barres 15 m (48 × 15 min ≈ 12 h de fond)
STOP_PCT = 0.015         # stop protecteur : 1,5 % contre la position
COOLDOWN_MIN = 60        # pas de nouvelle entrée dans les 60 min suivant une sortie


class BtcUsdt1m(PythonData):
    """Lecteur custom validé (donnees.py), inchangé : une ligne du CSV -> une barre LEAN."""

    def get_source(self, config, date, is_live):
        return SubscriptionDataSource(DATA_FILE, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or not line[0].isdigit():
            return None
        cols = line.split(",")
        bar = BtcUsdt1m()
        bar.symbol = config.symbol
        t_open = datetime.strptime(cols[0][:19], "%Y-%m-%d %H:%M:%S")
        bar.time = t_open
        bar.end_time = t_open + timedelta(minutes=1)
        bar.value = float(cols[4])
        bar["open"] = float(cols[1])
        bar["high"] = float(cols[2])
        bar["low"] = float(cols[3])
        bar["close"] = float(cols[4])
        bar["volume"] = float(cols[5])
        return bar


class FraisTakerBinance(FeeModel):
    """0,04 % du notionnel (taker Binance USDⓈ-M) — inchangé depuis buyhold.py."""

    def get_order_fee(self, parameters):
        notionnel = abs(parameters.order.quantity) * parameters.security.price
        return OrderFee(CashAmount(notionnel * FRAIS_TAKER, "USD"))


class SmaCroisement(QCAlgorithm):

    def initialize(self):
        # Bornes adaptatives : lues dans le CSV (jamais de dates figées)
        with open(DATA_FILE) as f:
            rows = f.read().splitlines()
        premier = datetime.strptime(rows[1][:19], "%Y-%m-%d %H:%M:%S")
        dernier = datetime.strptime(rows[-1][:19], "%Y-%m-%d %H:%M:%S")
        self.set_start_date(premier.year, premier.month, premier.day)
        self.set_end_date(dernier.year, dernier.month, dernier.day)
        self.set_cash(CAPITAL)
        self.set_time_zone(TimeZones.UTC)

        proprietes = SymbolProperties("BTCUSDT perpetuel USDS-M", "USD", 1,
                                      0.1, 0.00000001, "BTCUSDT")
        heures = SecurityExchangeHours.always_open(TimeZones.UTC)
        securite = self.add_data(BtcUsdt1m, "BTCUSDT", proprietes, heures,
                                 Resolution.MINUTE)
        securite.set_fee_model(FraisTakerBinance())
        self.btc = securite.symbol

        # ── Indicateurs de SIGNAL sur barres 15 m (nourris à la main aux bornes de 15 min).
        self.sma_rapide = SimpleMovingAverage(SMA_RAPIDE)
        self.sma_lente = SimpleMovingAverage(SMA_LENTE)
        self.diff_prec = None
        # ── Filtre de RÉGIME 15 m : SMA de fond nourrie des mêmes closes 15 m.
        self.sma_regime = SimpleMovingAverage(REGIME_N)
        self.dernier_close_sig = None

        # ── Gestion de position (long/short) + stop + cooldown
        self.prix_entree = None    # prix du dernier fill d'entrée (pour le stop)
        self.temps_sortie = None   # horodatage de la dernière sortie (pour le cooldown)
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def _regime(self):
        """+1 régime haussier, -1 baissier, 0 pas encore prêt."""
        if not self.sma_regime.is_ready or self.dernier_close_sig is None:
            return 0
        return 1 if self.dernier_close_sig > self.sma_regime.current.value else -1

    def _cooldown_ok(self, maintenant):
        return (self.temps_sortie is None
                or (maintenant - self.temps_sortie).total_seconds() >= COOLDOWN_MIN * 60)

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        bar = data[self.btc]
        close = float(bar.value)
        if self.premier_close is None:
            self.premier_close = close
        self.dernier_close = close
        t = bar.end_time

        # 1) Stop protecteur — vérifié à CHAQUE barre 1 m (extrême intra-barre), prioritaire.
        pos = self.portfolio[self.btc]
        if pos.invested and self.prix_entree is not None:
            bas, haut = float(bar["low"]), float(bar["high"])
            if pos.is_long and bas <= self.prix_entree * (1 - STOP_PCT):
                self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t
            elif pos.is_short and haut >= self.prix_entree * (1 + STOP_PCT):
                self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t

        # 2) SIGNAL : uniquement aux bornes de 15 min (barres 15 m « manuelles », causales).
        if t.minute % TF_SIGNAL != 0:
            return
        self.dernier_close_sig = close
        self.sma_rapide.update(t, close)
        self.sma_lente.update(t, close)
        self.sma_regime.update(t, close)
        if not (self.sma_rapide.is_ready and self.sma_lente.is_ready):
            return

        diff = self.sma_rapide.current.value - self.sma_lente.current.value
        regime = self._regime()
        pos = self.portfolio[self.btc]           # relire (le stop a pu liquider)
        if self.diff_prec is not None:
            croise_haut = self.diff_prec <= 0 and diff > 0
            croise_bas = self.diff_prec >= 0 and diff < 0
            if croise_haut:
                if regime > 0 and not pos.is_long and self._cooldown_ok(t):
                    self.set_holdings(self.btc, 1.0)          # long : croisement + régime haussier
                elif pos.is_short:
                    self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t
            elif croise_bas:
                if regime < 0 and not pos.is_short and self._cooldown_ok(t):
                    self.set_holdings(self.btc, -1.0)         # short : croisement + régime baissier
                elif pos.is_long:
                    self.liquidate(self.btc); self.prix_entree = None; self.temps_sortie = t
        self.diff_prec = diff

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            # mémorise le prix d'entrée quand on OUVRE/RENVERSE une position
            if self.portfolio[self.btc].invested:
                self.prix_entree = float(event.fill_price)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>3} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"qté={event.fill_quantity:+.8f} @ {event.fill_price} | "
                     f"frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement_strat = equite / CAPITAL - 1
        self.log(f"--- BILAN Croisement SMA {SMA_RAPIDE}/{SMA_LENTE} (signal 15 m, régime 12 h, "
                 f"long/short, cooldown {COOLDOWN_MIN}m) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
