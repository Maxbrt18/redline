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
from typing import Any

from redline.analysis.race_pace import SEGMENT_M, strava_2017
from redline.model import session_kinds, terrain
from redline.model.activity import Activity
from redline.model.streams import GAP_S, MOVING_MPS, WALK_CADENCE_PER_LEG, WALKED_SHARE

#: The exponent of the relative speed: the rTSS convention, to calibrate on the bone's
#: answer (the next morning's trace after long slow runs).
EXPONENT = 2
REFERENCE_WEEKS = 6
REFERENCE_MIN_RUNS = 3


@dataclass(frozen=True)
class Stretch:
    """A stretch of run: its moving time and its flat-equivalent speed; where it lies,
    its net grade, its mean HR over the moving samples (None without an HR stream) and
    its walked time (none without a cadence stream)."""

    moving_s: float
    flat_mps: float
    start_m: float = 0.0
    end_m: float = 0.0
    grade_pct: float = 0.0
    hr: float | None = None
    walked_s: float = 0.0

    @property
    def walked(self) -> bool:
        return self.moving_s > 0 and self.walked_s / self.moving_s >= WALKED_SHARE


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
    heart, cadence = streams.get("hr"), streams.get("cad")
    stretches: list[Stretch] = []
    dplus = dminus = 0.0
    start_k, moving, walked, beats, timed = 0, 0.0, 0.0, 0.0, 0.0
    for k in range(1, len(time)):
        step = time[k] - time[k - 1]
        if step <= GAP_S and (speed is None or (speed[k] or 0) >= MOVING_MPS):
            moving += step
            if cadence and cadence[k] is not None and cadence[k] < WALK_CADENCE_PER_LEG:
                walked += step
            if heart and heart[k] is not None:
                beats += heart[k] * step
                timed += step
        if altitude and altitude[k] is not None and altitude[k - 1] is not None:
            rise = altitude[k] - altitude[k - 1]
            dplus += max(rise, 0)
            dminus += max(-rise, 0)
        span = (distance[k] or 0) - (distance[start_k] or 0)
        if span >= SEGMENT_M or k == len(time) - 1:
            if span > 0 and moving > 0:
                grade = _grade(altitude, start_k, k, span)
                stretches.append(Stretch(
                    moving, span / moving / strava_2017(grade), distance[start_k] or 0.0,
                    distance[k] or 0.0, grade, beats / timed if timed else None, walked))
            start_k, moving, walked, beats, timed = k, 0.0, 0.0, 0.0, 0.0
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


def strava_id(key: str) -> int | None:
    """The Strava activity of a ledger row's idempotency key, if it came from Strava."""
    prefix = "strava:"
    if key.startswith(prefix) and key[len(prefix):].isdigit():
        return int(key[len(prefix):])
    return None


def group_of(store: Any, activity_id: int) -> bool | None:
    """Whether a captured run was run in a group; None when unknown, never assumed."""
    try:
        count = Activity.from_json(store.strava_intake(activity_id)["distilled"]).athlete_count
    except Exception:  # no capture: the group stays unknown, never assumed
        return None
    return None if count is None else int(count) > 1


def stored_profile(store: Any, activity_id: int) -> Profile | None:
    streams = store.strava_streams(activity_id)
    return None if not streams else profile(streams)


def easy_runs(store: Any, rows: Iterable[Any],
              day: dt.date) -> list[tuple[dt.date, Profile, bool | None]]:
    """The easy runs of the `REFERENCE_WEEKS` weeks before `day` with kept streams: their
    day, their stretches and whether they were run in a group."""
    since = day - dt.timedelta(weeks=REFERENCE_WEEKS)
    runs = []
    for row in rows:
        activity = strava_id(row.idempotency_key)
        if row.type != session_kinds.EASY or activity is None or not since <= row.date < day:
            continue
        shape = stored_profile(store, activity)
        if shape is not None:
            runs.append((row.date, shape, group_of(store, activity)))
    return runs


def reference_pace_s(store: Any, rows: Iterable[Any], day: dt.date) -> float | None:
    """The reference easy pace on `day`, in s/km, from the ledger and the kept streams."""
    speed = reference_mps(samples(easy_runs(store, rows, day)), day)
    return None if speed is None else 1000 / speed
