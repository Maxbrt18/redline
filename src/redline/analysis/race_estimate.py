"""A road goal's time, estimated from the efforts the athlete declared run all out.

The curve is Daniels and Gilbert's (1979): the VDOT of a performance is the oxygen cost of
its speed over the share of VO2max an athlete sustains for its duration, and the time at
another distance is the one with the same VDOT. That curve carries an average endurance.

The form behind the efforts is a local-level model. It is a hidden state drifting at
random by `drift` VDOT² a month; each flat effort (`redline.model.efforts`) measures it at
its date with a noise of `noise` VDOT, about the race-to-race variation of a runner's
times. A one-dimensional Kalman filter walks the efforts in date order: an old effort
keeps its value and loses certainty, a recent one weighs more, a single one stands alone
with a range. The share of each effort in the estimate is its coefficient in the filter.

The extrapolation adds its own margin: the further the source's distance from the goal's,
the more the average endurance weighs, `exponent_sd` × |ln(goal km / source km)| on the
log of the time, weighed by each source's share. It adds in variance to the form's.

A declared break of at least `break_min_days` after the last effort leaves only the slow
half of the range: the effort is then an upper bound of the form, until a new effort.

The sessions bound the form from below. A repetition or a run held to its end proves at
least its own VDOT: if that exceeds the estimate, the estimate is out of date. The other
way round is invisible to such bounds.

An estimate, never a goal: the goal is set in the interview, with `goal_source =
equivalence` when the athlete keeps this one. No estimate is given for a trail goal.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from redline.model import rulebook, session_kinds, terrain
from redline.model.efforts import Break, Effort

from .ledger import Row

#: The bisection's bounds and precision, in minutes.
SHORTEST_MIN, LONGEST_MIN, PRECISION_MIN = 1.0, 24 * 60.0, 1 / 600
#: Daniels and Gilbert fit their curve on races of about 3.5 minutes and more: a shorter
#: piece lies outside it and bounds nothing.
CURVE_MIN_MINUTES = 3.5
MONTH_DAYS = 365.25 / 12


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
class Settings:
    """The method's thresholds, read from the rulebook in force (`ESTIMATE_PARAMETERS`)."""

    window_weeks: int
    min_source_km: float
    noise: float
    drift: float
    exponent_sd: float
    band_pct: int
    break_min_days: int
    bound_weeks: int


def settings() -> Settings | None:
    """The thresholds declared by the rulebook, or None: no estimate is computed."""
    book = rulebook.current().parameters
    values = (book.estimate_window_weeks, book.estimate_min_source_km, book.estimate_noise_vdot,
              book.estimate_drift_vdot2_month, book.estimate_exponent_sd,
              book.estimate_band_pct, book.estimate_break_min_days, book.estimate_bound_weeks)
    if any(value is None for value in values):
        return None
    return Settings(*values)  # type: ignore[arg-type]


def months(start: dt.date, end: dt.date) -> float:
    return max((end - start).days, 0) / MONTH_DAYS


@dataclass(frozen=True)
class Source:
    """One effort behind an estimate."""

    effort: Effort
    vdot: float
    #: The goal distance's time at this effort's VDOT.
    equivalent_s: float
    #: Its coefficient in the filtered VDOT; the shares sum to 1.
    share: float
    months: float


@dataclass(frozen=True)
class Estimate:
    """The time at the goal distance, its range and the efforts it comes from."""

    vdot: float
    #: Standard deviation of the form today, in VDOT points.
    vdot_sd: float
    time_s: float
    low_s: float
    high_s: float
    band_pct: int
    sources: tuple[Source, ...]
    #: The declared break that makes the estimate an upper bound, if any.
    capped_by: Break | None = None


def usable(efforts: Iterable[Effort], today: dt.date, config: Settings) -> list[Effort]:
    """The efforts an equivalence reads: flat, long enough, within the window."""
    since = today - dt.timedelta(weeks=config.window_weeks)
    return sorted((effort for effort in efforts
                   if effort.flat and effort.km >= config.min_source_km
                   and since <= effort.on <= today),
                  key=lambda effort: (effort.on, effort.key))


def _filtered(efforts: Sequence[Effort], today: dt.date,
              config: Settings) -> tuple[float, float, list[float], list[float]]:
    """The filtered VDOT today, its variance, each effort's coefficient and VDOT."""
    noise = config.noise ** 2
    points = [vdot(effort.km, effort.seconds) for effort in efforts]
    mean, variance, shares = points[0], noise, [1.0]
    for previous, effort, point in zip(efforts, efforts[1:], points[1:], strict=False):
        variance += config.drift * months(previous.on, effort.on)
        gain = variance / (variance + noise)
        mean += gain * (point - mean)
        shares = [share * (1 - gain) for share in shares] + [gain]
        variance *= 1 - gain
    variance += config.drift * months(efforts[-1].on, today)
    return mean, variance, shares, points


def _cap(breaks: Iterable[Break], last: dt.date, today: dt.date,
         config: Settings) -> Break | None:
    """The latest declared break long enough, after the last effort and begun by today."""
    after = [pause for pause in breaks
             if pause.ends_on > last and pause.starts_on <= today
             and pause.days >= config.break_min_days]
    return max(after, key=lambda pause: pause.ends_on, default=None)


@dataclass(frozen=True)
class Form:
    """The filtered VDOT today, its standard deviation and the efforts it reads."""

    vdot: float
    vdot_sd: float
    efforts: tuple[Effort, ...]


def form(efforts: Iterable[Effort], today: dt.date, config: Settings) -> Form | None:
    """The athlete's form today from the declared all-out efforts; None without one."""
    found = usable(efforts, today, config)
    if not found:
        return None
    mean, variance, _, _ = _filtered(found, today, config)
    return Form(mean, math.sqrt(variance), tuple(found))


def estimate(efforts: Iterable[Effort], breaks: Iterable[Break], today: dt.date, km: float,
             config: Settings) -> Estimate | None:
    """The goal distance's time from the declared all-out efforts; None without one."""
    found = usable(efforts, today, config)
    if not found:
        return None
    mean, variance, shares, points = _filtered(found, today, config)
    deviation = math.sqrt(variance)
    time_s = time_for(mean, km)
    form = (math.log(time_for(mean - deviation, km))
            - math.log(time_for(mean + deviation, km))) / 2
    reach = config.exponent_sd * sum(share * abs(math.log(km / effort.km))
                                     for share, effort in zip(shares, found, strict=True))
    spread = statistics.NormalDist().inv_cdf(0.5 + config.band_pct / 200) * math.hypot(form, reach)
    capped_by = _cap(breaks, found[-1].on, today, config)
    low_s = time_s if capped_by is not None else time_s * math.exp(-spread)
    sources = tuple(Source(effort, point, time_for(point, km), share, months(effort.on, today))
                    for effort, point, share in zip(found, points, shares, strict=True))
    return Estimate(mean, deviation, time_s, low_s, time_s * math.exp(spread),
                    config.band_pct, sources, capped_by)


@dataclass(frozen=True)
class Bound:
    """The most a session of the recent weeks proves: at least this VDOT."""

    on: dt.date
    km: float
    seconds: float
    vdot: float


def bound(pieces: Iterable[tuple[dt.date, float, float]]) -> Bound | None:
    """The highest VDOT among pieces held to their end (date, km, seconds), within the
    curve's durations."""
    held = [Bound(on, km, seconds, vdot(km, seconds)) for on, km, seconds in pieces
            if km > 0 and seconds / 60 >= CURVE_MIN_MINUTES]
    return max(held, key=lambda found: found.vdot, default=None)


def pieces(store: Any, rows: Iterable[Row], today: dt.date,
           config: Settings) -> list[tuple[dt.date, float, float]]:
    """The flat runs of the recent weeks, whole, and the repetitions their laps show."""
    from . import easy_load

    since = today - dt.timedelta(weeks=config.bound_weeks)
    out: list[tuple[dt.date, float, float]] = []
    for row in rows:
        if not since <= row.date <= today or row.type not in session_kinds.BY_SLUG \
                or session_kinds.family(row.type) != "run" or not row.km or not row.hours \
                or terrain.of(row.km * 1000, row.dplus, row.dminus) is not terrain.Terrain.FLAT:
            continue
        out.append((row.date, row.km, row.hours * 3600))
        out.extend((row.date, rep["dist_m"] / 1000, rep["time_s"])
                   for rep in easy_load.stored_reps(store, row) or [])
    return out
