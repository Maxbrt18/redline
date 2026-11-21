"""The terrain of a stretch of run: climb, flat, descent or rolling.

Heart rate and pace read against a flat-ground threshold only where the ground is flat:
uphill the HR rises and the pace drops whatever the effort, downhill the pace rises.
A stretch is judged on its gain and its loss per kilometre, never on its net grade, which
reads a kilometre that goes up then down as flat.
"""

from __future__ import annotations

from enum import StrEnum

#: Above this gain or loss per kilometre, in metres, the relief drives HR and pace.
FLAT_M_PER_KM = 15


class Terrain(StrEnum):
    CLIMB = "climb"
    FLAT = "flat"
    DESCENT = "descent"
    ROLLING = "rolling"


def of(dist_m: float, dplus: float, dminus: float) -> Terrain:
    """The terrain of a stretch of `dist_m` metres that gains `dplus` and loses `dminus`."""
    if dist_m <= 0:
        return Terrain.ROLLING
    up = dplus * 1000 / dist_m > FLAT_M_PER_KM
    down = dminus * 1000 / dist_m > FLAT_M_PER_KM
    if up and down:
        return Terrain.ROLLING
    if up:
        return Terrain.CLIMB
    return Terrain.DESCENT if down else Terrain.FLAT
