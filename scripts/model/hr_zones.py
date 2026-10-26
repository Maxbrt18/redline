"""Les zones de fréquence cardiaque de l'athlète, en bpm, pour les calculs.

Le raisonnement et l'usage de chaque zone vivent dans `athlete/athlete.md` (§ Zones) :
méthode Karvonen, FC de repos 60, FC max ~199 **estimée** (jamais testée). Ce module en
porte les seules valeurs que du code consomme, parce qu'aucun code ne lit la prose. Les
zones par défaut de Strava (127/158/174/189) ne sont PAS celles du coaching : le plafond
d'EF (157) est le haut de la Z2 Karvonen.
"""

from __future__ import annotations

HR_REST_BPM = 60
#: Estimée, pas mesurée : à dire à côté de tout affichage qui en dépend.
HR_MAX_BPM = 199
#: Bornes basses de Z2 à Z5, en % de la réserve cardiaque.
RESERVE_SHARES = (0.60, 0.70, 0.80, 0.90)


def bounds(rest: int = HR_REST_BPM, peak: int = HR_MAX_BPM) -> tuple[int, ...]:
    """Les FC où commencent Z2, Z3, Z4 et Z5 (143, 157, 171, 185 aujourd'hui)."""
    return tuple(int(rest + share * (peak - rest)) for share in RESERVE_SHARES)


def zone(hr: float, limits: tuple[int, ...] | None = None) -> int:
    """La zone (1 à 5) d'une FC : une FC égale à une borne appartient à la zone du dessus."""
    edges = limits or bounds()
    return 1 + sum(1 for edge in edges if hr >= edge)
