"""Time on a race route from a flat-equivalent pace: the race curve of the route study.

The grade curve gives speed on a grade relative to the athlete's flat speed:

- **Climbs** follow Strava's 2017 grade-adjusted pace, built on equal heart rate over
  real-world runs (Robb, "An Improved GAP Model", 2017). STRAVA_2017 is a polynomial
  approximation of that published curve: speed factor 1.12 at -9 %, near 1 again around
  -18 %. On sustained race climbs it was the best of the curves tested.
- **Descents** follow the athlete's own factors when the instance declares them
  (`[pace] descent` in redline.toml), else the same Strava curve. Steep descents are bounded
  by braking, impact and control rather than energy.
- **Minetti et al. (2002)**, the treadmill energy cost, is not used: in races its descent
  speeds were wrong by 24 to 40 %.

The level is an input, never guessed: the flat-equivalent pace the athlete holds over the
whole race. MODEL_ERROR is the measured error of the method on the total time of hilly
runs whose level was known (median and 90th percentile, leave-one-run-out). Night, fatigue
after three hours and technical ground are not modelled; the plan says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from redline.model.errors import InputError

#: Length of the stretches the route is cut into to apply the curve, in metres.
SEGMENT_M = 200.0
#: |time error| of the method on hilly runs at known level: median, 90th percentile.
MODEL_ERROR = (0.04, 0.085)
#: Grades beyond this, in percent, are read at the limit: the curves are not measured there.
GRADE_LIMIT = 30.0


def strava_2017(grade_pct: float) -> float:
    """Speed factor of Strava's 2017 grade-adjusted pace, 1 on the flat."""
    i = max(-GRADE_LIMIT, min(GRADE_LIMIT, grade_pct)) / 100
    pace = (-80.918 * i**5 - 45.799 * i**4 + 6.9278 * i**3 + 19.332 * i**2
            + 2.7597 * i + 0.9792)
    return 0.9792 / pace


def _interpolate(points: Sequence[tuple[float, float]], grade_pct: float) -> float:
    grades = [grade for grade, _ in points]
    factors = [factor for _, factor in points]
    return float(np.interp(grade_pct, grades, factors))


@dataclass(frozen=True)
class GradeCurve:
    #: Declared descent factors (grade %, factor), empty for Strava's.
    descent: tuple[tuple[float, float], ...] = ()
    #: Declared training climb factors, empty when not declared.
    training_climb: tuple[tuple[float, float], ...] = ()

    def speed(self, grade_pct: float) -> float:
        if grade_pct < 0 and self.descent:
            return _interpolate(self.descent, grade_pct)
        return strava_2017(grade_pct)

    def fast_speed(self, grade_pct: float) -> float:
        """The fast bound: climbs as run in training, when declared."""
        if grade_pct > 0 and self.training_climb:
            return max(self.speed(grade_pct), _interpolate(self.training_climb, grade_pct))
        return self.speed(grade_pct)


@dataclass(frozen=True)
class AidStation:
    km: float
    name: str


@dataclass(frozen=True)
class Passage:
    km: float
    name: str
    #: Hours from the start: central, and the fast bound of the curve.
    hours: float
    fast_hours: float
    #: Hours running since the previous passage, stops excluded: what fuelling covers.
    running_hours: float


@dataclass(frozen=True)
class RacePlan:
    pace_s_per_km: float
    stop_minutes: float
    flat_equivalent_km: float
    passages: tuple[Passage, ...]
    #: The finish is the last passage.

    @property
    def finish(self) -> Passage:
        return self.passages[-1]


def parse_pace(text: str) -> float:
    """`5:30` -> 330 s/km."""
    minutes, _, seconds = text.partition(":")
    try:
        value = int(minutes) * 60 + (int(seconds) if seconds else 0)
    except ValueError:
        raise InputError(f"pace {text!r}: expected M:SS per km, e.g. 5:30") from None
    if not 150 <= value <= 1200 or (seconds and not 0 <= int(seconds) < 60):
        raise InputError(f"pace {text!r}: expected M:SS per km between 2:30 and 20:00")
    return float(value)


def parse_aid(text: str) -> AidStation:
    """`12:Le Camp` -> AidStation(12.0, "Le Camp")."""
    km, _, name = text.partition(":")
    try:
        return AidStation(float(km.replace(",", ".")), name.strip() or f"km {km}")
    except ValueError:
        raise InputError(f"aid station {text!r}: expected KM:NAME, e.g. 12:Le Camp") from None


def stretches(distance_m: np.ndarray, elevation_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(length in m, grade in %) of each SEGMENT_M stretch of a profile."""
    edges = np.append(np.arange(0.0, distance_m[-1], SEGMENT_M), distance_m[-1])
    heights = np.interp(edges, distance_m, elevation_m)
    lengths = np.diff(edges)
    keep = lengths > 0
    return lengths[keep], (np.diff(heights)[keep] / lengths[keep]) * 100


def plan(distance_m: np.ndarray, elevation_m: np.ndarray, curve: GradeCurve,
         pace_s_per_km: float, aids: Sequence[AidStation] = (),
         stop_minutes: float = 0.0) -> RacePlan:
    total_km = float(distance_m[-1]) / 1000
    for aid in aids:
        if not 0 < aid.km < total_km:
            raise InputError(f"aid station {aid.name!r} at km {aid.km:g} is off the route "
                             f"(0 to {total_km:.1f} km)")
    lengths, grades = stretches(distance_m, elevation_m)
    ends_km = np.cumsum(lengths) / 1000
    flat_speed = 1000 / pace_s_per_km
    central = np.cumsum(lengths / (flat_speed * np.array([curve.speed(g) for g in grades])))
    fast = np.cumsum(lengths / (flat_speed * np.array([curve.fast_speed(g) for g in grades])))
    marks = np.concatenate([[0.0], ends_km])

    def seconds_at(km: float, cumulative: np.ndarray) -> float:
        return float(np.interp(km, marks, np.concatenate([[0.0], cumulative])))

    passages = []
    previous = 0.0
    ordered = sorted(aids, key=lambda aid: aid.km)
    for index, aid in enumerate([*ordered, AidStation(total_km, "")]):
        stops = index * stop_minutes * 60
        running = seconds_at(aid.km, central)
        passages.append(Passage(aid.km, aid.name, (running + stops) / 3600,
                                (seconds_at(aid.km, fast) + stops) / 3600,
                                (running - previous) / 3600))
        previous = running
    return RacePlan(pace_s_per_km, stop_minutes,
                    float(np.sum(lengths / np.array([curve.speed(g) for g in grades]))) / 1000,
                    tuple(passages))
