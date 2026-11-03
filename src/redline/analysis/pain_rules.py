"""Rules n3 and n4 of the rulebook: the part the ledger can establish.

The two rules used to be applied by the model alone. Their triggers are facts the
ledger already holds, so the engine computes them and refuses a revision that ignores
them (`weekly_plan.pain_rule_errors`):

- n3: a zone's wake trace at or above `persistent_wake_min` on
  `persistent_wake_mornings` consecutive mornings is pain persisting beyond 48 h. From
  that morning, no quality session for `pain_no_quality_days` days, and a week planned
  before that morning targets `pain_cut_pct` % less volume.
- n4: `poor_nights_streak` consecutive poor nights, or an easy run whose HR at pace is
  abnormally high (`signals.hr_at_pace`), removes quality for `alarm_no_quality_days`.

What stays with the coach, because no reading holds it: stopping a session above the
cap or on an altered stride (it happens during the effort), the return steps after a
cut, a shorter run of poor nights, a gap in the morning readings, and how the rest of
an assimilation week is shaped.

A morning that was not read breaks a run: persistence is established by readings,
never assumed across a gap.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from statistics import mean

from redline.model import ledger as table
from redline.model import rulebook, session_kinds
from redline.model.vocabulary import Sleep
from redline.model.weekly_plan import QualityHold, VolumeCeiling

from .ledger import Row

TREND_DAYS = 14
#: Half the smallest step of the morning scale (0, 0.5, 1, 2): a smaller move between
#: two halves of the window is noise of the averaging, not a direction.
TREND_STEP = 0.25
#: Above this many episodes, a share of pain settled within 48 h can be read.
FALSIFY_MIN_EPISODES = 5
#: "Almost always": at or above this share, the n3 cap is too sensitive.
FALSIFY_SETTLED_SHARE = 0.9


@dataclass(frozen=True)
class WakeTrend:
    """One zone's morning readings over a window, its two halves compared."""

    zone: str
    days: int
    readings: tuple[tuple[dt.date, float], ...]
    earlier: float | None
    recent: float | None

    @property
    def direction(self) -> str | None:
        """rising, falling or stable; None when one half has no reading."""
        if self.earlier is None or self.recent is None:
            return None
        if self.recent - self.earlier > TREND_STEP:
            return "rising"
        if self.earlier - self.recent > TREND_STEP:
            return "falling"
        return "stable"


def _wakes(rows: list[Row], zone: str) -> list[tuple[dt.date, float]]:
    return sorted((row.date, value) for row in rows
                  if row.type == session_kinds.WAKE_TRACE
                  and (value := row.pain_at(zone)) is not None)


def wake_trend(rows: list[Row], zone: str, today: dt.date,
               days: int = TREND_DAYS) -> WakeTrend:
    """The morning readings of `zone` in the `days` up to `today`, halves compared."""
    start = today - dt.timedelta(days=days - 1)
    middle = start + dt.timedelta(days=days // 2)
    readings = tuple((day, value) for day, value in _wakes(rows, zone)
                     if start <= day <= today)
    earlier = [value for day, value in readings if day < middle]
    recent = [value for day, value in readings if day >= middle]
    return WakeTrend(zone, days, readings,
                     round(mean(earlier), 2) if earlier else None,
                     round(mean(recent), 2) if recent else None)


@dataclass(frozen=True)
class Persistence:
    """A run of mornings at or above the persistence level, long enough to count."""

    zone: str
    mornings: tuple[tuple[dt.date, float], ...]
    #: The morning the run reached its required length: the rule applies from here.
    established: dt.date

    def fact(self) -> str:
        """The evidence entry a revision cites when it applies rule n3."""
        days = ", ".join(f"{day.isoformat()} {value:g}" for day, value in self.mornings)
        return f"observation: wake-trace {self.zone} persistent ({days}) (rule n3)"


def persistences(rows: list[Row], zone: str, minimum: float,
                 mornings: int) -> list[Persistence]:
    """Every run of `mornings` consecutive mornings at or above `minimum`."""
    found: list[Persistence] = []
    run: list[tuple[dt.date, float]] = []
    for day, value in [*_wakes(rows, zone), (dt.date.max, -1.0)]:
        if value >= minimum and run and (day - run[-1][0]).days == 1:
            run.append((day, value))
            continue
        if len(run) >= mornings:
            found.append(Persistence(zone, tuple(run), run[mornings - 1][0]))
        run = [(day, value)] if value >= minimum else []
    return found


@dataclass(frozen=True)
class Settling:
    """What followed each effort above the n3 cap: the falsification of the rule."""

    settled: tuple[dt.date, ...]
    persisted: tuple[dt.date, ...]
    unread: tuple[dt.date, ...]

    @property
    def too_sensitive(self) -> bool | None:
        """True when pain above the cap almost always settles within 48 h."""
        read = len(self.settled) + len(self.persisted)
        if read < FALSIFY_MIN_EPISODES:
            return None
        return len(self.settled) / read >= FALSIFY_SETTLED_SHARE


def settling(rows: list[Row], zone: str, cap: float, minimum: float) -> Settling:
    """For each effort above `cap`: did a morning within 48 h fall below `minimum`?

    The two mornings after the effort are read; one below the persistence level means the
    pain settled. Both at or above it, the pain persisted. No reading, the episode is
    counted apart: an unread morning is not a settled one.
    """
    wakes = dict(_wakes(rows, zone))
    settled, persisted, unread = [], [], []
    for row in sorted(rows, key=lambda r: r.date):
        pain = row.pain_at(zone)
        if row.type not in table.EFFORT_TYPES or pain is None or pain <= cap:
            continue
        after = [wakes[day] for offset in (1, 2)
                 if (day := row.date + dt.timedelta(days=offset)) in wakes]
        if any(value < minimum for value in after):
            settled.append(row.date)
        elif len(after) == 2:
            persisted.append(row.date)
        else:
            unread.append(row.date)
    return Settling(tuple(settled), tuple(persisted), tuple(unread))


def poor_night_streaks(daily: list[dict], streak: int) -> list[tuple[dt.date, ...]]:
    """Every run of `streak` consecutive check-ins declaring a poor night."""
    found: list[tuple[dt.date, ...]] = []
    run: list[dt.date] = []
    poor = sorted(row["checked_on"] for row in daily if row.get("sleep") == Sleep.POOR)
    for day in [*poor, dt.date.max]:
        if run and (day - run[-1]).days == 1:
            run.append(day)
            continue
        if len(run) >= streak:
            found.append(tuple(run[:streak]))
        run = [day]
    return found


def holds(rows: list[Row], daily: list[dict], zones: list[str],
          today: dt.date) -> list[QualityHold]:
    """The windows, still open on `today`, in which rules n3 and n4 remove quality."""
    from redline.analysis import signals  # signals imports the ledger: deferred import

    parameters = rulebook.current().parameters
    found = []
    minimum, mornings = parameters.persistent_wake_min, parameters.persistent_wake_mornings
    if minimum is not None and mornings is not None and parameters.pain_no_quality_days:
        for zone in zones:
            for persistence in persistences(rows, zone, minimum, mornings):
                found.append(QualityHold("n3", persistence.established,
                                         persistence.established + dt.timedelta(
                                             days=parameters.pain_no_quality_days - 1),
                                         persistence.fact()))
    span = parameters.alarm_no_quality_days
    if span:
        if parameters.poor_nights_streak:
            for nights in poor_night_streaks(daily, parameters.poor_nights_streak):
                found.append(QualityHold(
                    "n4", nights[-1], nights[-1] + dt.timedelta(days=span - 1),
                    "observation: poor sleep on "
                    + ", ".join(day.isoformat() for day in nights) + " (rule n4)"))
        for drift in signals.hr_at_pace(rows):
            if drift.delta >= signals.HR_DRIFT_BPM:
                found.append(QualityHold(
                    "n4", drift.date, drift.date + dt.timedelta(days=span - 1),
                    f"observation: easy run {drift.date.isoformat()} HR {drift.hr:.0f} bpm "
                    f"for {drift.reference:.0f} at the same pace (rule n4)"))
    return [hold for hold in found if hold.end >= today and hold.start <= today]


def volume_ceilings(rows: list[Row], zones: list[str], week: str,
                    revisions: list[dict], today: dt.date) -> list[VolumeCeiling]:
    """Rule n3's cut on a week planned before persistence was established.

    The reference is the week's target in force the day before: the plan the athlete was
    on. A week first planned after that morning has no reference, and its volume is the
    coach's return steps.
    """
    parameters = rulebook.current().parameters
    minimum, mornings = parameters.persistent_wake_min, parameters.persistent_wake_mornings
    cut, span = parameters.pain_cut_pct, parameters.pain_no_quality_days
    if minimum is None or mornings is None or cut is None or not span:
        return []
    sunday = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 7)
    found = []
    for zone in zones:
        for persistence in persistences(rows, zone, minimum, mornings):
            if sunday < persistence.established or not (persistence.established <= today
                    < persistence.established + dt.timedelta(days=span)):
                continue
            before = [revision for revision in revisions
                      if revision["computed_at"][:10] < persistence.established.isoformat()
                      and revision.get("volume_target_km") is not None]
            if before:
                reference = before[-1]["volume_target_km"]
                found.append(VolumeCeiling("n3", week, round(reference * (100 - cut) / 100, 1),
                                           reference, persistence.fact()))
    return found
