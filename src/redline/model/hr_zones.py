"""Les zones de fréquence cardiaque, en bpm, pour les calculs.

Méthode Karvonen sur la FC de repos et la FC max de l'athlète, déclarées dans
`redline.toml` (`[athlete] hr_rest`, `hr_max`) ; le raisonnement et l'usage de chaque
zone vivent dans le profil de l'athlète. Ce module ne porte que la méthode, jamais les
valeurs d'un athlète. Les zones par défaut de Strava ne sont PAS celles du coaching : le
plafond d'EF est le haut de la Z2 Karvonen.
"""

from __future__ import annotations

#: Bornes basses de Z2 à Z5, en % de la réserve cardiaque.
RESERVE_SHARES = (0.60, 0.70, 0.80, 0.90)


def bounds(rest: int, peak: int) -> tuple[int, ...]:
    """Les FC où commencent Z2, Z3, Z4 et Z5."""
    return tuple(int(rest + share * (peak - rest)) for share in RESERVE_SHARES)


def zone(hr: float, limits: tuple[int, ...]) -> int:
    """La zone (1 à 5) d'une FC : une FC égale à une borne appartient à la zone du dessus."""
    return 1 + sum(1 for edge in limits if hr >= edge)
