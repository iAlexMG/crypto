# Définition UNIQUE des sessions de trading (heure de New York).
#
# Source de vérité importée par le générateur de features, les diagnostics et les
# figures : UNE seule liste d'ouvertures, et des étiquettes de légende DÉRIVÉES de
# cette liste — une légende ne peut plus contredire le découpage réel (c'est le bug
# « Asia 20:00-02:59 » de fig_volume_profile_sessions, corrigé le 2026-07-05).
#
#   Asia   18:00 -> 02:59 NY   |  London 03:00 -> 09:29 NY  |  NY 09:30 -> 16:59 NY
#   (17:00 -> 17:59 NY : hors session — niveaux GELÉS de la dernière session)
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
# Ouvertures de session, en heure de New York (minuit -> minuit)
OUVERTURES_NY = [(3, 0, "london"), (9, 30, "ny"), (17, 0, "hors"), (18, 0, "asia")]
NOM = {"asia": "Asia", "london": "London", "ny": "NY", "hors": "hors"}


def bornes_sessions(debut_ms: int, fin_ms: int) -> list[tuple[int, str]]:
    """Toutes les ouvertures de session converties en UTC ms, triées.
    Calculées JOUR NY par JOUR NY -> le passage à l'heure d'été est respecté
    (09:30 NY = 14:30 UTC en hiver, 13:30 UTC en été)."""
    bornes: list[tuple[int, str]] = []
    jour = datetime.fromtimestamp(debut_ms / 1000, tz=NY).date() - timedelta(days=1)
    fin = datetime.fromtimestamp(fin_ms / 1000, tz=NY).date() + timedelta(days=1)
    while jour <= fin:
        for h, m, nom in OUVERTURES_NY:
            t = datetime(jour.year, jour.month, jour.day, h, m, tzinfo=NY)
            bornes.append((int(t.timestamp() * 1000), nom))
        jour += timedelta(days=1)
    bornes.sort()
    return bornes


def plages_ny() -> dict[str, str]:
    """{nom: "HH:MM-HH:MM NY"} — plages d'affichage DÉRIVÉES des ouvertures
    (fin = ouverture suivante - 1 min, en heure NY)."""
    tri = sorted(OUVERTURES_NY)
    plages: dict[str, str] = {}
    for i, (h, m, nom) in enumerate(tri):
        h2, m2, _ = tri[(i + 1) % len(tri)]
        fin = (h2 * 60 + m2 - 1) % (24 * 60)
        plages[nom] = f"{h:02d}:{m:02d}-{fin // 60:02d}:{fin % 60:02d} NY"
    return plages
