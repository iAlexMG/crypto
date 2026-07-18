# Volume profile PAR SESSION + analyse des volumes sur BTCUSDT — SCALPING 1 m, long/short.
# DEUX flux de données custom dans le même algo :
#   - le prix (1m.csv, lecteur validé des leçons précédentes) ;
#   - les features de profil (features_vp.csv : session, barres, delta, POC/VAH/VAL du
#     profil de la session EN COURS — Asia/London/NY, heure de New York — développé
#     MINUTE PAR MINUTE), reconstruites par backtests/volume_profile_features.py.
# Lecture retenue : ACCEPTATION SYMÉTRIQUE de la value area de LA SESSION —
#   - cassure du HAUT (VAH) par le haut + delta acheteur -> LONG  (prix acceptés au-dessus) ;
#   - cassure du BAS (VAL) par le bas + delta vendeur    -> SHORT (prix acceptés en dessous).
# Le profil fournit lui-même la cible et le stop (dérivés des niveaux, pas des % fixes).
# Règles de session (a priori) : pas d'entrée hors session ni dans les MIN_BARRES premières
# minutes d'une session (profil embryonnaire) ; pas de croisement détecté À CHEVAL sur deux
# enchères (les niveaux sautent au reset). Long/short -> plus de face-à-face Buy & Hold.
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Mêmes constantes / même source de vérité que les autres leçons
DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
VP_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/features_vp.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000

# ── Paramètres de la stratégie (choisis A PRIORI — leçon 07 : jamais après la courbe)
# RE-CALIBRAGE anti-frais (2026-07-17) : la v1 sur-tradait (726 ordres, 25 k$). Leviers :
# edge exigé doublé (0,4 %), profil moins embryonnaire (15 min), cooldown 45 min.
FILTRE_DELTA = True      # n'entrer que si l'EMA du delta va dans le sens du trade
DELTA_SPAN = 60          # période de l'EMA du delta (60 barres = 60 min en cadence 1 m)
TP_FRAC = 1.0            # cible : fraction du chemin niveau->projection (1.0 = chemin entier)
STOP_FRAC = 0.5          # stop : fraction du chemin AU-DELÀ du niveau (retour = hypothèse morte)
MIN_EDGE = 0.004         # edge minimal 0,4 % du chemin niveau->cible (mesuré du niveau franchi,
                         # pas du fill au close — qui, déjà au-delà, en mange une partie)
MIN_BARRES = 15          # pas d'entrée avant la 15e MINUTE d'une session (profil embryonnaire)
COOLDOWN_MIN = 45        # pas de nouvelle entrée dans les 45 min après une sortie
TRICHE_LOOKAHEAD = False # ⚠️ falsification leçon 09 : livrer le profil 1 min TROP TOT


class BtcUsdt1m(PythonData):
    """Lecteur prix validé (donnees.py), inchangé."""

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


class VpFeatures(PythonData):
    """Second flux : la ligne t porte le profil de la session en cours, développé de
    l'ouverture de session à la clôture de la MINUTE t (incluse).
    end_time = t+1min -> LEAN la livre à la clôture de la barre t, dans le MEME Slice
    que la barre de prix t : causal par construction. (TRICHE_LOOKAHEAD la livre à
    l'OUVERTURE t, une minute trop tôt -> biais de futur, mesuré dans la leçon.)
    Colonnes : time,session,barres,delta,poc,vah,val"""

    def get_source(self, config, date, is_live):
        return SubscriptionDataSource(VP_FILE, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or not line[0].isdigit():
            return None
        cols = line.split(",")
        if cols[4] == "":                 # tout début d'historique : aucun profil encore
            return None
        bar = VpFeatures()
        bar.symbol = config.symbol
        t_open = datetime.strptime(cols[0][:19], "%Y-%m-%d %H:%M:%S")
        bar.time = t_open
        bar.end_time = t_open if TRICHE_LOOKAHEAD else t_open + timedelta(minutes=1)
        bar.value = float(cols[4])        # POC (value obligatoire, jamais de NaN)
        bar["session"] = cols[1]          # asia | london | ny | hors
        bar["barres"] = float(cols[2])    # ancienneté du profil dans la session (minutes)
        bar["delta"] = float(cols[3])
        bar["poc"] = float(cols[4])
        bar["vah"] = float(cols[5])
        bar["val"] = float(cols[6])
        return bar


class FraisTakerBinance(FeeModel):
    """0,04 % du notionnel (taker Binance USDⓈ-M) — inchangé."""

    def get_order_fee(self, parameters):
        notionnel = abs(parameters.order.quantity) * parameters.security.price
        return OrderFee(CashAmount(notionnel * FRAIS_TAKER, "USD"))


class VolumeProfile(QCAlgorithm):

    def initialize(self):
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

        # Le flux de features n'est PAS un instrument tradé, mais il lui faut le même
        # cadre 24/7 UTC que le prix (surcharge validée leçon 03) pour que ses
        # timestamps soient lus en UTC.
        props_vp = SymbolProperties("Features volume profile", "USD", 1,
                                    0.1, 0.00000001, "BTCVP")
        self.vp = self.add_data(VpFeatures, "BTCVP", props_vp,
                                SecurityExchangeHours.always_open(TimeZones.UTC),
                                Resolution.MINUTE).symbol

        # EMA du delta tenue à la main (lissage standard 2/(N+1)) : l'analyse des volumes.
        # ⚠️ ne PAS l'appeler self.alpha : `alpha` est une propriété .NET de QCAlgorithm
        # (le modèle alpha du framework) — l'affectation planterait initialize.
        self.lissage = 2.0 / (DELTA_SPAN + 1)
        self.delta_ema = None

        self.poc = self.vah = self.val = None   # niveaux courants du profil de session
        self.session = None          # session active (asia | london | ny | hors)
        self.barres = 0              # ancienneté du profil dans la session (minutes)
        self.close_prec = None
        self.vah_prec = None
        self.val_prec = None
        self.temps_sortie = None     # horodatage de la dernière sortie (pour le cooldown)
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def _cooldown_ok(self, maintenant):
        return (self.temps_sortie is None
                or (maintenant - self.temps_sortie).total_seconds() >= COOLDOWN_MIN * 60)

    def on_data(self, data: Slice):
        # 1) Le profil d'abord : rafraîchir session, niveaux et EMA du delta.
        if self.vp in data:
            f = data[self.vp]
            if str(f["session"]) != self.session:
                # Nouvelle enchère : les niveaux SAUTENT (profil remis à zéro) -> on ne
                # détecte jamais un « franchissement » à cheval sur deux sessions.
                self.session = str(f["session"])
                self.close_prec = None
                self.vah_prec = None
                self.val_prec = None
            self.barres = int(float(f["barres"]))
            self.poc, self.vah, self.val = float(f["poc"]), float(f["vah"]), float(f["val"])
            delta = float(f["delta"])
            self.delta_ema = delta if self.delta_ema is None else \
                self.lissage * delta + (1 - self.lissage) * self.delta_ema

        # 2) Puis le prix : signaux au close de la barre clôturée.
        if self.btc not in data:
            return
        close = float(data[self.btc].value)
        if self.premier_close is None:
            self.premier_close = close        # AVANT le warmup : trace de référence
        self.dernier_close = close
        if self.poc is None:
            return                            # warmup du profil glissant
        poc, vah, val = self.poc, self.vah, self.val
        pos = self.portfolio[self.btc]

        if not pos.invested:
            # Entrée : le close FRANCHIT un bord de la value area (événement), DANS une
            # session dont le profil a au moins MIN_BARRES minutes, l'edge du chemin
            # couvre les frais, et les agresseurs vont dans le sens du trade.
            session_ok = (self.session != "hors" and self.barres >= MIN_BARRES
                          and self._cooldown_ok(self.time))
            if session_ok and self.close_prec is not None:
                # LONG : cassure de VAH par le haut (acceptation au-dessus de la valeur).
                if (self.vah_prec is not None and self.close_prec <= self.vah_prec
                        and close > vah):
                    amp = vah - poc                       # projection = demi-largeur haute
                    cible = vah + TP_FRAC * amp
                    edge_ok = amp > 0 and (cible - vah) / vah >= MIN_EDGE
                    delta_ok = (not FILTRE_DELTA) or (self.delta_ema is not None
                                                      and self.delta_ema > 0)
                    if edge_ok and delta_ok:
                        self.set_holdings(self.btc, 1.0)
                        self.log(f"ENTREE LONG  {self.time:%Y-%m-%d %H:%M} [{self.session} "
                                 f"m{self.barres}] | close={close} > vah={vah:.0f} | "
                                 f"cible={cible:.0f} | delta_ema={self.delta_ema:+.0f}")
                # SHORT : cassure de VAL par le bas (acceptation en dessous de la valeur).
                elif (self.val_prec is not None and self.close_prec >= self.val_prec
                        and close < val):
                    amp = poc - val
                    cible = val - TP_FRAC * amp
                    edge_ok = amp > 0 and (val - cible) / val >= MIN_EDGE
                    delta_ok = (not FILTRE_DELTA) or (self.delta_ema is not None
                                                      and self.delta_ema < 0)
                    if edge_ok and delta_ok:
                        self.set_holdings(self.btc, -1.0)
                        self.log(f"ENTREE SHORT {self.time:%Y-%m-%d %H:%M} [{self.session} "
                                 f"m{self.barres}] | close={close} < val={val:.0f} | "
                                 f"cible={cible:.0f} | delta_ema={self.delta_ema:+.0f}")
        else:
            # Sorties sur les niveaux COURANTS du profil (externes à la position) :
            # cible atteinte, ou retour au-delà du niveau conquis (hypothèse invalidée).
            if pos.is_long:
                amp = vah - poc
                cible = vah + TP_FRAC * amp
                stop = vah - STOP_FRAC * amp
                if close >= cible:
                    self.liquidate(self.btc); self.temps_sortie = self.time
                    self.log(f"SORTIE cible {self.time:%Y-%m-%d %H:%M} | close={close} >= {cible:.0f}")
                elif close <= stop:
                    self.liquidate(self.btc); self.temps_sortie = self.time
                    self.log(f"SORTIE stop  {self.time:%Y-%m-%d %H:%M} | close={close} <= {stop:.0f}")
            else:                                          # short
                amp = poc - val
                cible = val - TP_FRAC * amp
                stop = val + STOP_FRAC * amp
                if close <= cible:
                    self.liquidate(self.btc); self.temps_sortie = self.time
                    self.log(f"SORTIE cible {self.time:%Y-%m-%d %H:%M} | close={close} <= {cible:.0f}")
                elif close >= stop:
                    self.liquidate(self.btc); self.temps_sortie = self.time
                    self.log(f"SORTIE stop  {self.time:%Y-%m-%d %H:%M} | close={close} >= {stop:.0f}")

        self.close_prec, self.vah_prec, self.val_prec = close, vah, val

    def on_order_event(self, event: OrderEvent):
        if event.status == OrderStatus.FILLED:
            self.nb_trades += 1
            self.frais_totaux += float(event.order_fee.value.amount)
            sens = "ACHAT " if event.fill_quantity > 0 else "VENTE "
            self.log(f"TRADE {self.nb_trades:>3} {sens}{event.utc_time:%Y-%m-%d %H:%M} UTC | "
                     f"qté={event.fill_quantity:+.8f} @ {event.fill_price} | "
                     f"frais={event.order_fee.value.amount:.2f} $")

    def on_end_of_algorithm(self):
        equite = float(self.portfolio.total_portfolio_value)
        rendement = equite / CAPITAL - 1
        self.log(f"--- BILAN Volume profile 1 m (acceptation VAH/VAL, filtre delta={FILTRE_DELTA}, "
                 f"triche={TRICHE_LOOKAHEAD}), long/short ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement:+.4%}")
