# Croisement de moyennes mobiles — SCALPING 1 m, multi-TF, long/short.
# PATRON de la refonte scalping (2026-07 : re-calibrage validé par l'utilisateur).
#   - Entrée : croisement SMA 9/21 sur les barres 1 m.
#   - Filtre de régime : SMA 5 m (échantillonnée aux bornes de 5 min) — on n'entre
#     QUE dans le sens de la tendance supérieure (long si régime haussier, short si baissier).
#   - Sortie : croisement inverse OU stop protecteur (~0,5 %).
#   - Long ET short autorisés -> N'EST PLUS directement comparable au Buy & Hold.
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Même source de vérité que donnees.py / buyhold.py (OHLCV 1 m)
DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
FRAIS_TAKER = 0.0004      # 0,04 % Binance USDⓈ-M — LA constante de frais de la formation
CAPITAL = 100_000

# ── Paramètres scalping (choisis A PRIORI — leçon 07 : jamais après la courbe)
SMA_RAPIDE = 9            # SMA courte, barres 1 m
SMA_LENTE = 21           # SMA longue, barres 1 m
REGIME_5M = 50           # SMA de régime sur barres 5 m (50 × 5 min ≈ 4 h de tendance)
STOP_PCT = 0.005         # stop protecteur : 0,5 % contre la position


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

        # ── Indicateurs 1 m : croisement rapide/lent (mis à jour à la main sur close clôturé)
        self.sma_rapide = SimpleMovingAverage(SMA_RAPIDE)
        self.sma_lente = SimpleMovingAverage(SMA_LENTE)
        self.diff_prec = None

        # ── Filtre de régime 5 m : SMA nourrie du close des barres 1 m qui TOMBENT sur
        #    une borne de 5 min (00,05,10…). C'est la version « manuelle » d'un
        #    consolidateur — transparente, causale (barre 1 m déjà clôturée).
        self.sma_regime = SimpleMovingAverage(REGIME_5M)
        self.dernier_close_5m = None

        # ── Gestion de position (long/short) + stop
        self.prix_entree = None    # prix du dernier fill d'entrée (pour le stop)
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def _regime(self):
        """+1 régime haussier, -1 baissier, 0 pas encore prêt."""
        if not self.sma_regime.is_ready or self.dernier_close_5m is None:
            return 0
        return 1 if self.dernier_close_5m > self.sma_regime.current.value else -1

    def on_data(self, data: Slice):
        if self.btc not in data:
            return
        bar = data[self.btc]
        close = float(bar.value)
        if self.premier_close is None:
            self.premier_close = close
        self.dernier_close = close

        # 1) Régime 5 m : n'échantillonner qu'aux bornes de 5 min (end_time = ouverture+1min ;
        #    une barre 1 m [10:04,10:05) clôture à 10:05 -> minute 5 -> échantillon 5 m).
        if bar.end_time.minute % 5 == 0:
            self.dernier_close_5m = close
            self.sma_regime.update(bar.end_time, close)

        # 2) Indicateurs 1 m
        self.sma_rapide.update(bar.end_time, close)
        self.sma_lente.update(bar.end_time, close)
        if not (self.sma_rapide.is_ready and self.sma_lente.is_ready):
            return

        # 3) Stop protecteur AVANT le signal (prioritaire) : coupe si la position saigne.
        #    Déclenché sur l'EXTRÊME intra-barre (low si long, high si short) — plus honnête
        #    que le seul close ; le fill reste au close (convention de la formation).
        pos = self.portfolio[self.btc]
        if pos.invested and self.prix_entree is not None:
            bas, haut = float(bar["low"]), float(bar["high"])
            if pos.is_long and bas <= self.prix_entree * (1 - STOP_PCT):
                self.liquidate(self.btc); self.prix_entree = None
            elif pos.is_short and haut >= self.prix_entree * (1 + STOP_PCT):
                self.liquidate(self.btc); self.prix_entree = None

        # 4) Signal de croisement 1 m, filtré par le régime 5 m
        diff = self.sma_rapide.current.value - self.sma_lente.current.value
        regime = self._regime()
        if self.diff_prec is not None:
            croise_haut = self.diff_prec <= 0 and diff > 0
            croise_bas = self.diff_prec >= 0 and diff < 0
            if croise_haut:
                if regime > 0 and not pos.is_long:
                    self.set_holdings(self.btc, 1.0)          # long : croisement + régime haussier
                elif pos.is_short:
                    self.liquidate(self.btc); self.prix_entree = None   # au moins couper le short
            elif croise_bas:
                if regime < 0 and not pos.is_short:
                    self.set_holdings(self.btc, -1.0)         # short : croisement + régime baissier
                elif pos.is_long:
                    self.liquidate(self.btc); self.prix_entree = None
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
        self.log(f"--- BILAN Croisement SMA {SMA_RAPIDE}/{SMA_LENTE} (1 m, régime 5 m, long/short) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement_strat:+.4%}")
