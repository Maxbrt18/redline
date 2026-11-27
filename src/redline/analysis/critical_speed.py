"""The athlete's critical speed and D′, fitted on the efforts declared run all out.

The two-parameter model: distance = CS × time + D′, a straight line through the flat
all-out efforts of the recent weeks (`redline.model.efforts`). CS is the boundary between
the heavy and the severe domains, the best estimate of the maximal metabolic steady
state (Jones et al. 2019): the upper limit of a threshold session. Above it, a repetition
draws on D′, the distance the athlete can cover beyond CS, and fails once D′ is spent.

The line holds over efforts of about 2 to 20 minutes, with at least 5 minutes between
the shortest and the longest (the usual practice of critical-speed testing). Below CS
the model predicts an endless effort, which is wrong past half an hour: longer durations
read Daniels' curve (`redline.analysis.race_estimate`). Two efforts give the line and no
error; three or more give the standard error of CS. CS is reproducible (a coefficient of
variation near 0.4 %), D′ much less (near 13 %; Galbraith et al. 2014).

The domains around CS come from the rulebook: the first threshold, the top of an easy
run, at a share of CS (82 % on average across 26 studies, Hunter et al. 2024), and the
band a threshold session is run in, below CS, a convention to check against the
athlete's heart rate and RPE.

The test the coach may propose: 1,200 m then 3,600 m all out, flat, in one session with
30 minutes of recovery. Proposed, never imposed.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from redline.model import rulebook
from redline.model.efforts import Effort

#: The durations the two-parameter model holds over, and the spread it needs, in minutes.
FIT_MIN_MINUTES, FIT_MAX_MINUTES, FIT_MIN_SPREAD_MINUTES = 2.0, 20.0, 5.0


@dataclass(frozen=True)
class Settings:
    """The method's thresholds, read from the rulebook in force."""

    weeks: int
    first_threshold_pct: float
    threshold_low_pct: float
    threshold_high_pct: float


def settings() -> Settings | None:
    """The thresholds declared by the rulebook, or None: no critical speed is read."""
    book = rulebook.current().parameters
    values = (book.critical_speed_weeks, book.critical_speed_first_threshold_pct,
              book.critical_speed_threshold_low_pct, book.critical_speed_threshold_high_pct)
    if any(value is None for value in values):
        return None
    return Settings(*values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Fit:
    """The line through the efforts: CS in m/s, D′ in metres."""

    speed_mps: float
    d_prime_m: float
    efforts: tuple[Effort, ...]
    #: Standard error of CS in m/s; None with two efforts, which leave no residual.
    speed_se: float | None = None

    @property
    def pace_s(self) -> float:
        """CS as a pace, in seconds per kilometre."""
        return 1000 / self.speed_mps

    def pace_at(self, pct: float) -> float:
        """The pace of `pct` % of CS, in seconds per kilometre."""
        return 1000 / (self.speed_mps * pct / 100)


def usable(efforts: Iterable[Effort], today: dt.date, weeks: int) -> list[Effort]:
    """The flat efforts of the recent weeks whose duration the model holds over."""
    since = today - dt.timedelta(weeks=weeks)
    return [effort for effort in efforts
            if effort.flat and since <= effort.on <= today
            and FIT_MIN_MINUTES <= effort.minutes <= FIT_MAX_MINUTES]


def fit(efforts: Iterable[Effort], today: dt.date, config: Settings) -> Fit | None:
    """CS and D′ by least squares; None without two efforts spread enough, or when the
    line is not physical (CS or D′ not positive)."""
    found = sorted(usable(efforts, today, config.weeks), key=lambda effort: effort.seconds)
    if len(found) < 2 or found[-1].minutes - found[0].minutes < FIT_MIN_SPREAD_MINUTES:
        return None
    times = [effort.seconds for effort in found]
    metres = [effort.km * 1000 for effort in found]
    mean_t, mean_d = statistics.fmean(times), statistics.fmean(metres)
    spread = sum((time - mean_t) ** 2 for time in times)
    speed = sum((time - mean_t) * (distance - mean_d)
                for time, distance in zip(times, metres, strict=True)) / spread
    d_prime = mean_d - speed * mean_t
    if speed <= 0 or d_prime <= 0:
        return None
    error = None
    if len(found) > 2:
        residuals = [distance - d_prime - speed * time
                     for time, distance in zip(times, metres, strict=True)]
        error = math.sqrt(sum(r * r for r in residuals) / (len(found) - 2) / spread)
    return Fit(speed, d_prime, tuple(sorted(found, key=lambda effort: effort.on)), error)
