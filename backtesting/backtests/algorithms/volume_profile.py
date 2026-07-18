# Volume profile PAR SESSION + analyse des volumes sur BTCUSDT.
# DEUX flux de données custom dans le même algo :
#   - le prix (1m.csv, lecteur validé des leçons précédentes) ;
#   - les features de profil (features_vp.csv : session, barres, delta, POC/VAH/VAL du
#     profil de la session EN COURS — Asia/London/NY, heure de New York — développé
#     barre par barre), reconstruites par backtests/volume_profile_features.py.
# Lecture retenue : `vah_break` — cassure du haut de la value area de LA SESSION = le
# marché accepte des prix au-dessus de la valeur de l'enchère en cours, confirmée par
# un delta acheteur. Le profil fournit lui-même la cible et le stop.
# Règles de session (a priori) : pas d'entrée hors session ni dans les 2 premières
# barres d'une session (profil embryonnaire) ; pas de croisement détecté À CHEVAL sur
# deux enchères (les niveaux sautent au reset).
from AlgorithmImports import *
from datetime import datetime, timedelta

# ── Mêmes constantes / même source de vérité que les autres leçons
DATA_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/1m.csv"
VP_FILE = "H:/Crypto/historique/ohlcv/BTCUSDT-um/features_vp.csv"
FRAIS_TAKER = 0.0004
CAPITAL = 100_000

# ── Paramètres de la stratégie (choisis A PRIORI — leçon 07 : jamais après la courbe)
LECTURE = "vah_break"    # "vah_break" (acceptance/tendance) ou "val_reclaim" (contre-tendance)
FILTRE_DELTA = True      # n'entrer que si l'EMA du delta est acheteuse (> 0)
DELTA_SPAN = 24          # période de l'EMA du delta (24 barres = 24 h)
TP_FRAC = 1.0            # cible : fraction du chemin lo->hi (1.0 = le chemin entier)
STOP_FRAC = 0.5          # stop : fraction du chemin SOUS lo (retour = hypothèse morte)
MIN_EDGE = 0.002         # edge minimal 0,2 % du chemin lo->cible (mesuré du niveau franchi,
                         # pas du fill au close — qui, déjà au-dessus de lo, en mange une partie)
MIN_BARRES = 3           # pas d'entrée avant la 3e barre d'une session (profil embryonnaire)
TRICHE_LOOKAHEAD = False # ⚠️ falsification leçon 09 : livrer le profil 1 h TROP TOT


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
    l'ouverture de session à la clôture de la barre t (incluse).
    end_time = t+1h -> LEAN la livre à la clôture de la barre t, dans le MEME Slice
    que la barre de prix t : causal par construction. (TRICHE_LOOKAHEAD la livre à
    l'OUVERTURE t, une heure trop tôt -> biais de futur, mesuré dans la leçon.)
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
        bar["barres"] = float(cols[2])    # ancienneté du profil dans la session
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

        self.niveaux = None          # (lo, cible, stop) de la barre courante
        self.session = None          # session active (asia | london | ny | hors)
        self.barres = 0              # ancienneté du profil dans la session
        self.close_prec = None
        self.lo_prec = None
        self.nb_trades = 0
        self.frais_totaux = 0.0
        self.premier_close = None
        self.dernier_close = None

    def on_data(self, data: Slice):
        # 1) Le profil d'abord : rafraîchir session, niveaux et EMA du delta.
        if self.vp in data:
            f = data[self.vp]
            if str(f["session"]) != self.session:
                # Nouvelle enchère : les niveaux SAUTENT (profil remis à zéro) -> on ne
                # détecte jamais un « franchissement » à cheval sur deux sessions.
                self.session = str(f["session"])
                self.close_prec = None
                self.lo_prec = None
            self.barres = int(float(f["barres"]))
            poc, vah, val = float(f["poc"]), float(f["vah"]), float(f["val"])
            delta = float(f["delta"])
            self.delta_ema = delta if self.delta_ema is None else \
                self.lissage * delta + (1 - self.lissage) * self.delta_ema
            if LECTURE == "vah_break":        # chemin travaillé : vah -> vah + (vah - poc)
                lo, hi = vah, vah + (vah - poc)
            else:                             # val_reclaim : chemin val -> poc
                lo, hi = val, poc
            self.niveaux = (lo, lo + TP_FRAC * (hi - lo), lo - STOP_FRAC * (hi - lo))

        # 2) Puis le prix : signaux au close de la barre clôturée.
        if self.btc not in data:
            return
        close = float(data[self.btc].value)
        if self.premier_close is None:
            self.premier_close = close        # AVANT le warmup : B&H comparable aux autres leçons
        self.dernier_close = close
        if self.niveaux is None:
            return                            # warmup du profil glissant (23 barres)
        lo, cible, stop = self.niveaux

        if not self.portfolio[self.btc].invested:
            # Entrée : le close FRANCHIT lo par le bas (un événement, pas un état),
            # DANS une session dont le profil a au moins MIN_BARRES barres, l'edge du
            # chemin lo->cible couvre les frais (l'entrée réelle au close, déjà
            # au-dessus de lo, en mange une partie), et les agresseurs sont acheteurs.
            franchit = (self.close_prec is not None and self.lo_prec is not None
                        and self.close_prec <= self.lo_prec and close > lo)
            session_ok = self.session != "hors" and self.barres >= MIN_BARRES
            edge_ok = (cible - lo) / lo >= MIN_EDGE
            delta_ok = (not FILTRE_DELTA) or (self.delta_ema is not None
                                              and self.delta_ema > 0)
            if franchit and session_ok and edge_ok and delta_ok:
                self.set_holdings(self.btc, 1.0)
                self.log(f"ENTREE {self.time:%Y-%m-%d %H:%M} [{self.session} b{self.barres}] "
                         f"| close={close} > lo={lo:.0f} | cible={cible:.0f} "
                         f"stop={stop:.0f} | delta_ema={self.delta_ema:+.0f}")
        else:
            # Sorties sur les niveaux COURANTS du profil (externes à la position) :
            # cible atteinte, ou retour sous le niveau conquis (hypothèse invalidée).
            if close >= cible:
                self.liquidate(self.btc)
                self.log(f"SORTIE cible {self.time:%Y-%m-%d %H:%M} | close={close} >= {cible:.0f}")
            elif close <= stop:
                self.liquidate(self.btc)
                self.log(f"SORTIE stop  {self.time:%Y-%m-%d %H:%M} | close={close} <= {stop:.0f}")

        self.close_prec, self.lo_prec = close, lo

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
        rendement_bh = self.dernier_close / self.premier_close - 1
        self.log(f"--- BILAN Volume profile ({LECTURE}, filtre delta={FILTRE_DELTA}, "
                 f"triche={TRICHE_LOOKAHEAD}) ---")
        self.log(f"Trades exécutés : {self.nb_trades} | frais totaux : {self.frais_totaux:.2f} $")
        self.log(f"Équité finale : {equite:.2f} $ | rendement stratégie : {rendement:+.4%}")
        self.log(f"Buy & Hold (close/close) : {rendement_bh:+.4%} | "
                 f"écart stratégie - B&H : {rendement - rendement_bh:+.4%}")
