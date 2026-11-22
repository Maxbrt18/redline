"""The run lineages, read from the plan and the ledger: what the progression engine proposes.

A run lineage follows the sessions of one planned kind (`workout`, a session of
repetitions) declared in `[progression]` of the instance's `rulebook.toml`. Its dose is
the typed dose of the planned session (`reps`, `rep_seconds`, `recovery_seconds`), read
from the revision in force on the day it was run; its readings are the declared RPE
against the range its dose expects, the execution gaps of the debrief verdict, the
reserve, the pain of the session, the next morning and the runs that follow.

A rise passes no veto: the weeks of the taper, an assimilation week, a quality hold of
rules n3 and n4, a gate that did not open on the closed week before.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from redline.model import iso_week, rulebook, weekly_plan
from redline.model.progression import (
    RUN_FAMILIES,
    Dose,
    Exposure,
    Family,
    Lineage,
    Proposal,
    Verdict,
    Veto,
    VetoKind,
    default,
)
from redline.model.vocabulary import FindingOwner, SessionStatus

from . import easy_load, expected_rpe, ledger, pain_rules
from .progression import judge, propose
from .progression_readings import gate_exposure, interval_exposure
from .progression_strength import vetoes as calendar_vetoes


def planned_dose(family: Family, session: dict) -> Dose | None:
    """The typed dose of a planned session on the ladder of its family, or None."""
    values = [(variable.name, float(session[variable.name])) for variable in family.ladder
              if session.get(variable.name) is not None]
    return Dose(tuple(values)) if values and session.get("reps") is not None else None


@dataclass
class Plans:
    """The revisions in force, week by week, read once from the store."""

    store: Any

    def __post_init__(self) -> None:
        self._weeks: dict[str, list[dict]] = {}

    def sessions(self, week: str) -> list[dict]:
        if week not in self._weeks:
            plan = self.store.load_plan(week)
            self._weeks[week] = list(weekly_plan.active(plan)["sessions"]) if plan else []
        return self._weeks[week]

    def session_on(self, workout: str, day: dt.date) -> dict | None:
        """The session of `workout` planned on `day`, as the revision in force holds it."""
        return next((session for session in self.sessions(iso_week.of(day))
                     if session["workout"] == workout and session["date"] == day.isoformat()
                     and session.get("status") != SessionStatus.CANCELLED), None)


def _gaps(store: Any, row: ledger.Row) -> float | None:
    """The athlete's execution gaps in the kept verdict; None without a verdict."""
    activity = easy_load.strava_id(row.idempotency_key)
    found = store.debrief_verdict(activity) if activity is not None else None
    if not found:
        return None
    return float(sum(1 for finding in found["findings"] or ()
                     if finding.get("owner") == FindingOwner.ATHLETE))


def _over_range(history: expected_rpe.History | None, row: ledger.Row) -> float | None:
    if history is None or row.rpe is None:
        return None
    expected = history.expectation(row)
    return None if expected is None else row.rpe - expected.high


def exposures(store: Any, rows: Sequence[ledger.Row], lineage: Lineage, family: Family,
              plans: Plans, zones: Sequence[str],
              history: expected_rpe.History | None) -> list[Exposure]:
    """The sessions of the lineage's workout run with a typed dose, oldest first."""
    found = []
    for row in sorted(rows, key=lambda item: item.date):
        if row.type != lineage.workout:
            continue
        session = plans.session_on(lineage.workout, row.date)
        dose = planned_dose(family, session) if session is not None else None
        if dose is None:
            continue
        found.append(interval_exposure(rows, row, dose, zones, row.reserve,
                                       over_range=_over_range(history, row),
                                       gaps=_gaps(store, row)))
    return found


def current_dose(family: Family, lineage: Lineage, plans: Plans, week: str) -> Dose | None:
    """The latest typed dose the plans give the workout, the target week first."""
    for offset in range(9):
        sessions = sorted(plans.sessions(iso_week.shift(week, -offset)),
                          key=lambda session: session["date"], reverse=True)
        for session in sessions:
            if session["workout"] == lineage.workout and (
                    dose := planned_dose(family, session)) is not None:
                return dose
    return None


def vetoes(store: Any, book: rulebook.Rulebook, rows: Sequence[ledger.Row], week: str,
           today: dt.date, zones: Sequence[ledger.PainZone]) -> tuple[Veto, ...]:
    """The taper, assimilation, quality holds and a gate not green, for `week`."""
    found = list(calendar_vetoes(store, book, week, today))
    start, end = iso_week.monday(week), iso_week.monday(week) + dt.timedelta(days=6)
    for hold in pain_rules.holds(list(rows), store.daily_rows(), [zone.slug for zone in zones],
                                 today):
        if hold.start <= end and hold.end >= start:
            found.append(Veto(VetoKind.QUALITY_HOLD, hold.rule, hold.fact))
    closed = iso_week.shift(week, -1)
    instr = ledger.load_instruments(store).get(closed, ledger.Instruments())
    gate = Lineage("gate", "gate", (), (("gate_effort_peak", book.parameters.gate_effort_peak),))
    verdicts = {item.verdict for item in judge(
        default()["gate"], gate, gate_exposure(list(rows), closed, instr, list(zones), today))}
    if verdicts & {Verdict.FAIL, Verdict.UNKNOWN}:
        found.append(Veto(VetoKind.GATE, "po", f"gate of {closed} not green"))
    return tuple(found)


def proposals(store: Any, book: rulebook.Rulebook, today: dt.date,
              week: str | None = None) -> list[Proposal]:
    """One proposal per declared run lineage, in declaration order, for `week`."""
    declared = [lineage for lineage in book.progression.lineages
                if lineage.family in RUN_FAMILIES]
    if not declared:
        return []
    week = week or iso_week.of(today)
    families = default()
    rows = ledger.parse_ledger(store)
    zones = ledger.pain_zones(store)
    config = expected_rpe.settings()
    history = expected_rpe.history(store, rows, config) if config is not None else None
    plans = Plans(store)
    vetoed = vetoes(store, book, rows, week, today, zones)
    found = []
    for lineage in declared:
        family = families[lineage.family]
        found.append(propose(
            family, lineage,
            exposures(store, rows, lineage, family, plans, [zone.slug for zone in zones],
                      history),
            current=current_dose(family, lineage, plans, week), vetoes=vetoed, today=today,
            lookback_days=book.parameters.transition_lookback_days))
    return found
