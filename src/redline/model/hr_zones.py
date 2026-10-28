"""Heart rate zones, in bpm, for calculations.

Karvonen method on the athlete's resting and maximum heart rate, declared in
`redline.toml` (`[athlete] hr_rest`, `hr_max`); the reasoning and use of each zone live
in the athlete's profile. This module carries only the method, never an athlete's
values. Strava's default zones are NOT the coaching ones: the easy-run cap is the top of
the Karvonen Z2.
"""

from __future__ import annotations

#: Lower bounds of Z2 to Z5, as a share of the heart rate reserve.
RESERVE_SHARES = (0.60, 0.70, 0.80, 0.90)


def bounds(rest: int, peak: int) -> tuple[int, ...]:
    """The heart rates where Z2, Z3, Z4 and Z5 start."""
    return tuple(int(rest + share * (peak - rest)) for share in RESERVE_SHARES)


def zone(hr: float, limits: tuple[int, ...]) -> int:
    """The zone (1 to 5) of a heart rate; a rate equal to a bound belongs to the zone above."""
    return 1 + sum(1 for edge in limits if hr >= edge)
