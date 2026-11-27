"""What a cycle's trame is generated from, read once from the store.

The only reader of the store for the plan generator (`redline.model.plan_generator`): it
gathers, for a cycle starting the week after `today`,
- where the athlete stands: the mean weekly running volume of the closed weeks before,
  the planned assimilation weeks left out, raised to the rebuild's floor, and the longest
  run of the last 30 days;
- the highest weekly volume held over `cycle_held_weeks` closed weeks;
- the minutes the athlete's weekly habits leave for running, when every running day
  states them, and their easy pace over the recent weeks;
- the calibration in force, and whether it is measured and recent;
- the dose each run lineage stands at.

And where the athlete stands for the transitions (`standing`): the same start before its
floor, without the weeks a cycle planned low.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Collection
from typing import Any

from redline.model import calibration as calibration_model
from redline.model import iso_week, rulebook, session_kinds, week_days
from redline.model.cycle_templates import CycleTemplates
from redline.model.cycle_transitions import Standing
from redline.model.errors import ConfigError
from redline.model.plan_generator import Inputs
from redline.model.progression import Dose
from redline.model.vocabulary import CalibrationConfidence, DayKind

from . import ledger, progression_runs

#: The closed weeks the starting volume is the mean of.
START_WEEKS = 3
#: The closed weeks the easy pace is read over.
EASY_PACE_WEEKS = 6
#: Habits that leave no running that day.
NO_RUN = frozenset({DayKind.OFF})


def _volumes(rows: list[ledger.Row], weeks: list[str]) -> list[float]:
    return [ledger.week_volume([row for row in rows if row.week == week]).km_run
            for week in weeks]


def _minutes(store: Any, first: dt.date) -> int | None:
    """The minutes the habits leave for running in the week from `first`; None when a
    running day states no limit."""
    days = [first + dt.timedelta(days=offset) for offset in range(7)]
    habits = store.habits(days[0], days[-1])
    total = 0
    for day in days:
        habit = week_days.habit_on(habits, day)
        if habit is None:
            return None
        if habit["kind"] in NO_RUN or (habit["kind"] == DayKind.OTHER_SPORT
                                       and not habit.get("run_too")):
            continue
        if habit.get("minutes") is None:
            return None
        total += int(habit["minutes"])
    return total


def _easy_pace(rows: list[ledger.Row], today: dt.date) -> float | None:
    since = today - dt.timedelta(weeks=EASY_PACE_WEEKS)
    paces = [row.hours * 3600 / row.km for row in rows
             if row.type == session_kinds.EASY and row.km and row.hours
             and since <= row.date <= today]
    return statistics.median(paces) if paces else None


def _lineages(store: Any, book: rulebook.Rulebook, today: dt.date) -> tuple[tuple[str, Dose],
                                                                           ...]:
    found = []
    for proposal in progression_runs.proposals(store, book, today):
        lineage = book.progression.of(proposal.lineage)
        dose = proposal.ceiling or proposal.dose
        if lineage is not None and lineage.workout and dose is not None:
            found.append((lineage.workout, dose))
    return tuple(found)


def declared() -> bool:
    """Whether the rulebook declares what a trame is generated with."""
    parameters = rulebook.current().parameters
    return (parameters.cycle_held_weeks is not None
            and parameters.skeleton_ramp_max_pct is not None
            and parameters.skeleton_long_step_max_pct is not None)


def _parameters() -> rulebook.Parameters:
    parameters = rulebook.current().parameters
    if not declared():
        raise ConfigError("rulebook.toml declares no cycle thresholds (cycle_held_weeks, "
                          "skeleton_ramp_max_pct, skeleton_long_step_max_pct)")
    return parameters


def _closed(today: dt.date, parameters: rulebook.Parameters) -> list[str]:
    """The closed weeks the start reads, assimilation weeks left out."""
    current = iso_week.of(today)
    assimilation = set(parameters.assimilation_weeks)
    return [week for week in (iso_week.shift(current, -offset)
                              for offset in range(1, START_WEEKS + 1))
            if week not in assimilation]


def _held(rows: list[ledger.Row], today: dt.date, parameters: rulebook.Parameters) -> float:
    current = iso_week.of(today)
    assert parameters.cycle_held_weeks is not None
    weeks = [iso_week.shift(current, -offset)
             for offset in range(1, parameters.cycle_held_weeks + 1)]
    return max(_volumes(rows, weeks), default=0.0)


def _mean(rows: list[ledger.Row], weeks: list[str]) -> float | None:
    return statistics.fmean(_volumes(rows, weeks)) if weeks else None


def standing(store: Any, templates: CycleTemplates, today: dt.date,
             planned_low: Collection[str]) -> Standing:
    """Where the athlete stands on `today`; `planned_low`: the weeks a cycle planned low."""
    parameters = _parameters()
    rows = ledger.parse_ledger(store)
    closed = _closed(today, parameters)
    floor = _held(rows, today, parameters) * templates.rebuild.floor_pct / 100
    return Standing(round(_mean(rows, closed) or 0.0, 1),
                    _round(_mean(rows, [week for week in closed if week not in planned_low])),
                    round(floor, 1))


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def inputs(store: Any, templates: CycleTemplates, today: dt.date) -> Inputs:
    """The inputs of a cycle starting the week after `today`."""
    book = rulebook.current()
    parameters = _parameters()
    assert parameters.skeleton_ramp_max_pct is not None
    assert parameters.skeleton_long_step_max_pct is not None
    rows = ledger.parse_ledger(store)
    current = iso_week.of(today)
    held = _held(rows, today, parameters)
    start = max(_mean(rows, _closed(today, parameters)) or 0.0,
                held * templates.rebuild.floor_pct / 100)
    found = calibration_model.in_force(store.calibration_rows())
    fresh = parameters.calibration_fresh_weeks
    calibrated = (found is not None and found.confidence == CalibrationConfidence.MEASURED
                  and fresh is not None and (today - found.adopted_on).days <= fresh * 7)
    origin = iso_week.shift(current, 1)
    return Inputs(origin, round(start, 1), ledger.longest_recent(rows, today) or 0.0,
                  round(held, 1), parameters.skeleton_ramp_max_pct,
                  parameters.skeleton_long_step_max_pct,
                  _minutes(store, iso_week.monday(origin)), _easy_pace(rows, today),
                  calibrated, found.adopted_on if found else None,
                  _lineages(store, book, today))
