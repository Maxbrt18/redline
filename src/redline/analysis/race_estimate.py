"""A road race's time, estimated from a recent race: Daniels' VDOT equivalence.

The source is the athlete's best recent race (`race` rows of the ledger) on flat ground,
of at least `MIN_SOURCE_KM` and run within the `RECENT_WEEKS` weeks before the day. The
VDOT of a performance (Daniels and Gilbert, 1979) is the oxygen cost of its speed over
the share of VO2max an athlete sustains for its duration; the time at another distance
is the one with the same VDOT. It holds between road races run all out: a source on
hilly ground gives none, and the caller asks for no estimate of a trail race.

An estimate, never a goal: the goal is set in the interview, with `goal_source =
equivalence` when the athlete keeps this one.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable
from dataclasses import dataclass

from redline.model import session_kinds, terrain

from .ledger import Row

#: A shorter race says more about speed than about the endurance a goal race needs.
MIN_SOURCE_KM = 3.0
RECENT_WEEKS = 26
#: The bisection's bounds and precision, in minutes.
SHORTEST_MIN, LONGEST_MIN, PRECISION_MIN = 1.0, 24 * 60.0, 1 / 600


def oxygen_cost(metres_per_minute: float) -> float:
    """VO2 in ml/kg/min at a running speed (Daniels and Gilbert)."""
    return -4.60 + 0.182258 * metres_per_minute + 0.000104 * metres_per_minute ** 2


def sustained_share(minutes: float) -> float:
    """The share of VO2max sustained for a race of this duration (Daniels and Gilbert)."""
    return (0.8 + 0.1894393 * math.exp(-0.012778 * minutes)
            + 0.2989558 * math.exp(-0.1932605 * minutes))


def vdot(km: float, seconds: float) -> float:
    minutes = seconds / 60
    return oxygen_cost(km * 1000 / minutes) / sustained_share(minutes)


def time_for(points: float, km: float) -> float:
    """The time, in seconds, that gives `points` of VDOT over `km`: the faster a time,
    the higher its VDOT, so a bisection finds it."""
    low, high = SHORTEST_MIN, LONGEST_MIN
    while high - low > PRECISION_MIN:
        middle = (low + high) / 2
        if vdot(km, middle * 60) > points:
            low = middle
        else:
            high = middle
    return (low + high) / 2 * 60


@dataclass(frozen=True)
class Estimate:
    """The time at the goal distance, and the race it comes from."""

    source_on: dt.date
    source_km: float
    source_s: float
    vdot: float
    time_s: float


def estimate(rows: Iterable[Row], today: dt.date, km: float) -> Estimate | None:
    """The goal distance's time from the best recent flat race; None without one."""
    since = today - dt.timedelta(weeks=RECENT_WEEKS)
    sources = [row for row in rows
               if row.type == session_kinds.RACE and since <= row.date < today
               and row.km and row.km >= MIN_SOURCE_KM and row.hours
               and terrain.of(row.km * 1000, row.dplus, row.dminus) is terrain.Terrain.FLAT]
    if not sources:
        return None
    best = max(sources, key=lambda row: vdot(row.km or 0.0, (row.hours or 0.0) * 3600))
    points = vdot(best.km or 0.0, (best.hours or 0.0) * 3600)
    return Estimate(best.date, best.km or 0.0, (best.hours or 0.0) * 3600, points,
                    time_for(points, km))
