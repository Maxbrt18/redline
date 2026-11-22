"""The load of an easy run: equivalent minutes at the athlete's reference easy pace.

An envelope of 50 minutes reads "50 minutes at your usual easy pace": slower, the run
lasts longer; faster, shorter. A run is cut into stretches of `SEGMENT_M` metres; each
stretch counts its moving time times (flat-equivalent speed / reference speed) squared,
the pace-based load convention (rTSS: duration × intensity²). The flat-equivalent speed
reads the grade through Strava's 2017 curve (`race_pace.strava_2017`), so a climb run
slowly is not counted as an easy stretch.

The reference speed is dated: the median flat-equivalent speed of the solo, flat easy runs
of the `REFERENCE_WEEKS` weeks before the run, and none below `REFERENCE_MIN_RUNS` of
them. A group run is shaped by the others, a hilly one by its route: neither says what
pace the athlete picks.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from redline.analysis.race_pace import SEGMENT_M, strava_2017
from redline.model import terrain
from redline.model.streams import GAP_S, MOVING_MPS

#: The exponent of the relative speed: the rTSS convention, to calibrate on the bone's
#: answer (the next morning's trace after long slow runs).
EXPONENT = 2
REFERENCE_WEEKS = 6
REFERENCE_MIN_RUNS = 3


@dataclass(frozen=True)
class Stretch:
    """A stretch of run: its moving time and its flat-equivalent speed."""

    moving_s: float
    flat_mps: float


@dataclass(frozen=True)
class Profile:
    """What the streams of one run say about its load."""

    stretches: tuple[Stretch, ...]
    distance_m: float
    dplus_m: float
    dminus_m: float

    @property
    def moving_s(self) -> float:
        return sum(stretch.moving_s for stretch in self.stretches)

    @property
    def flat(self) -> bool:
        return terrain.of(self.distance_m, self.dplus_m, self.dminus_m) == terrain.Terrain.FLAT

    @property
    def flat_mps(self) -> float | None:
        """The run's flat-equivalent speed: the distance it is worth on the flat, per
        moving second."""
        moving = self.moving_s
        if not moving:
            return None
        return sum(stretch.flat_mps * stretch.moving_s for stretch in self.stretches) / moving

    def equivalent_minutes(self, reference_mps: float) -> float:
        return sum(stretch.moving_s * (stretch.flat_mps / reference_mps) ** EXPONENT
                   for stretch in self.stretches) / 60


def profile(streams: dict) -> Profile | None:
    """The stretches of a run from its kept streams; None without time or distance."""
    time, distance = streams.get("t"), streams.get("d")
    if not time or not distance or len(distance) != len(time):
        return None
    speed, altitude = streams.get("v"), streams.get("alt")
    stretches: list[Stretch] = []
    dplus = dminus = 0.0
    start_k, moving = 0, 0.0
    for k in range(1, len(time)):
        step = time[k] - time[k - 1]
        if step <= GAP_S and (speed is None or (speed[k] or 0) >= MOVING_MPS):
            moving += step
        if altitude and altitude[k] is not None and altitude[k - 1] is not None:
            rise = altitude[k] - altitude[k - 1]
            dplus += max(rise, 0)
            dminus += max(-rise, 0)
        span = (distance[k] or 0) - (distance[start_k] or 0)
        if span >= SEGMENT_M or k == len(time) - 1:
            if span > 0 and moving > 0:
                stretches.append(Stretch(moving, span / moving / strava_2017(
                    _grade(altitude, start_k, k, span))))
            start_k, moving = k, 0.0
    total = (distance[-1] or 0) - (distance[0] or 0)
    return Profile(tuple(stretches), total, dplus, dminus) if stretches else None


def _grade(altitude: list | None, start: int, end: int, span: float) -> float:
    if not altitude or altitude[start] is None or altitude[end] is None:
        return 0.0
    return (altitude[end] - altitude[start]) / span * 100


@dataclass(frozen=True)
class Sample:
    """One easy run that may set the reference: solo and flat."""

    date: dt.date
    flat_mps: float


def samples(runs: Iterable[tuple[dt.date, Profile, bool | None]]) -> list[Sample]:
    """The runs that set the reference: known solo (`group` False) and flat."""
    out = []
    for day, run, group in runs:
        speed = run.flat_mps
        if group is False and run.flat and speed:
            out.append(Sample(day, speed))
    return out


def reference_mps(found: Iterable[Sample], day: dt.date) -> float | None:
    """The reference speed on `day`: the median of the samples of the weeks before it."""
    since = day - dt.timedelta(weeks=REFERENCE_WEEKS)
    speeds = [sample.flat_mps for sample in found if since <= sample.date < day]
    return statistics.median(speeds) if len(speeds) >= REFERENCE_MIN_RUNS else None
