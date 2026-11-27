"""The run lineages, read from the plan and the ledger: what the progression engine proposes.

A run lineage follows the sessions of one planned kind (`workout`, a session of
repetitions) declared in `[progression]` of the instance's `rulebook.toml`. Its dose is
the typed dose of the planned session (`reps`, `rep_seconds`, `recovery_seconds`), read
from the revision in force on the day it was run; its readings are the declared RPE
against the range its dose expects, the execution gaps of the debrief verdict, the
reserve, the pain of the session, the next morning and the runs that follow.

A rise passes no veto: the weeks of the taper, an assimilation week, a quality hold of
rules n3 and n4, a gate that did not open on the closed week before.

The tally (`reviews`) says what followed each rise a lineage ran and each bounded
override: the falsification of its step, a suggestion the declaration changes only by
decision.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import progression as text
from redline.model import iso_week, prescription, rulebook, weekly_plan
from redline.model.progression import (
    RUN_FAMILIES,
    Dose,
    Exposure,
    Family,
    Lineage,
    Proposal,
    RiseOutcome,
    Verdict,
    Veto,
    VetoKind,
    default,
)
from redline.model.vocabulary import FindingOwner, OverrideReason, SessionStatus

from . import easy_load, expected_rpe, ledger, pain_rules
from .progression import (
    TALLY_MIN_RISES,
    StepTally,
    above,
    judge,
    outcome,
    propose,
    rises,
    step_up,
    tally,
)
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


def athlete_gaps(findings: Sequence[dict]) -> float:
    """The athlete's execution gaps among a verdict's findings, as kept (`as_json`)."""
    return float(sum(1 for finding in findings if finding.get("owner") == FindingOwner.ATHLETE))


def over_range(expected: expected_rpe.Expectation | None, rpe: float | None) -> float | None:
    """The declared RPE minus the top of the range its dose expects; None without either."""
    return None if expected is None or rpe is None else rpe - expected.high


def _gaps(store: Any, row: ledger.Row) -> float | None:
    """The athlete's execution gaps in the kept verdict; None without a verdict."""
    activity = easy_load.strava_id(row.idempotency_key)
    found = store.debrief_verdict(activity) if activity is not None else None
    if not found:
        return None
    return athlete_gaps(found["findings"] or ())


def _over_range(history: expected_rpe.History | None, row: ledger.Row) -> float | None:
    if history is None:
        return None
    return over_range(history.expectation(row) if row.rpe is not None else None, row.rpe)


def runs(store: Any, rows: Sequence[ledger.Row], lineage: Lineage, family: Family,
         plans: Plans, zones: Sequence[str],
         history: expected_rpe.History | None) -> list[tuple[dict, Exposure]]:
    """The sessions of the lineage's workout run with a typed dose, oldest first, each
    with the planned session it ran."""
    found = []
    for row in sorted(rows, key=lambda item: item.date):
        if row.type != lineage.workout:
            continue
        session = plans.session_on(lineage.workout, row.date)
        dose = planned_dose(family, session) if session is not None else None
        if session is None or dose is None:
            continue
        found.append((session, interval_exposure(rows, row, dose, zones, row.reserve,
                                                 over_range=_over_range(history, row),
                                                 gaps=_gaps(store, row))))
    return found


def exposures(store: Any, rows: Sequence[ledger.Row], lineage: Lineage, family: Family,
              plans: Plans, zones: Sequence[str],
              history: expected_rpe.History | None) -> list[Exposure]:
    """The sessions of the lineage's workout run with a typed dose, oldest first."""
    return [exposure for _, exposure in runs(store, rows, lineage, family, plans, zones,
                                             history)]


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


def _changed(session: dict, before: dict | None) -> bool:
    """A dose given by this revision, not carried unchanged from the previous one."""
    names = (*prescription.DOSE_FIELDS, *prescription.OVERRIDE_FIELDS, "date")
    return before is None or any(session.get(name) != before.get(name) for name in names)


def override_errors(store: Any, book: rulebook.Rulebook, week: str, revision: dict,
                    previous: dict | None, today: dt.date) -> list[str]:
    """The typed dose of each quality session a revision gives, against its lineage.

    At or under the ceiling, no override is declared. One step above, the bounded
    override: no veto in force, a typed reason, an open prediction sealed on it, and never
    twice in a row on the lineage. Beyond one step, or under a veto, refused.
    """
    lineages = {lineage.workout: lineage for lineage in book.progression.lineages
                if lineage.family in RUN_FAMILIES}
    sessions = [session for session in revision.get("sessions") or []
                if session.get("workout") in lineages and session.get("reps") is not None
                and session.get("status") != SessionStatus.CANCELLED]
    before = {session["id"]: session for session in (previous or {}).get("sessions") or []}
    sessions = [session for session in sessions if _changed(session, before.get(session["id"]))]
    if not sessions:
        return []
    found = {proposal.lineage: proposal for proposal in proposals(store, book, today, week)}
    rows = ledger.parse_ledger(store)
    vetoed = vetoes(store, book, rows, week, today, ledger.pain_zones(store))
    open_keys = {row["prediction_key"] for row in store.prediction_rows()
                 if row.get("status") == "open"}
    plans = Plans(store)
    families = default()
    problems = []
    for session in sessions:
        lineage = lineages[session["workout"]]
        family, proposal = families[lineage.family], found[lineage.slug]
        where = f"{week} revision {revision.get('revision')}.{session['id']}"
        dose, ceiling = planned_dose(family, session), proposal.ceiling
        declared = session.get("override_reason") is not None
        if dose is None or ceiling is None or not above(family, dose, ceiling):
            if declared:
                problems.append(f"{where}: no override at or under the ceiling of "
                                f"{lineage.slug} ({ceiling})")
            continue
        step = step_up(family, lineage, ceiling)
        if step is None or above(family, dose, step):
            problems.append(f"{where}: {dose} is more than one step above the ceiling of "
                            f"{lineage.slug} ({ceiling})")
            continue
        if vetoed:
            problems.append(f"{where}: {dose} is above the ceiling of {lineage.slug} "
                            f"({proposal.ceiling}) under a veto: "
                            + "; ".join(f"{veto.rule} {veto.fact}" for veto in vetoed))
            continue
        if not declared:
            problems.append(f"{where}: {dose} is one step above the ceiling of {lineage.slug} "
                            f"({proposal.ceiling}) -- declare override_reason and the "
                            "override_prediction sealed on it, or prescribe at most the "
                            "ceiling")
            continue
        if session["override_prediction"] not in open_keys:
            problems.append(f"{where}.override_prediction: no open prediction "
                            f"{session['override_prediction']}")
        last = _last_planned(plans, lineage, dt.date.fromisoformat(session["date"]), week)
        if last is not None and last.get("override_reason") is not None:
            problems.append(f"{where}: the previous {lineage.workout} of {lineage.slug} "
                            f"({last['date']}) was already an override; never twice in a row")
    return problems


def _last_planned(plans: Plans, lineage: Lineage, before: dt.date, week: str) -> dict | None:
    """The latest planned session of the lineage's workout with a typed dose before
    `before`, within the transition window's weeks."""
    for offset in range(6):
        candidates = sorted((session for session in plans.sessions(iso_week.shift(week, -offset))
                             if session["workout"] == lineage.workout
                             and session.get("reps") is not None
                             and session.get("status") != SessionStatus.CANCELLED
                             and session["date"] < before.isoformat()),
                            key=lambda session: session["date"], reverse=True)
        if candidates:
            return candidates[0]
    return None


@dataclass(frozen=True)
class Override:
    """A session run one step above its lineage's ceiling, and what followed it."""

    on: dt.date
    reason: OverrideReason
    prediction: str
    followed: RiseOutcome
    #: The sealed prediction, once resolved: borne out or refuted; None while open.
    borne_out: bool | None


@dataclass(frozen=True)
class Review:
    """The tally of a run lineage's steps and of its overrides."""

    lineage: str
    tally: StepTally
    overrides: tuple[Override, ...]


def _borne_out(row: dict | None) -> bool | None:
    if row is None or row.get("status") != "resolved" or row.get("outcome") is None:
        return None
    return bool(row["outcome"])


def reviews(store: Any, book: rulebook.Rulebook) -> list[Review]:
    """What followed each rise and each override of the declared run lineages.

    Two lineages that rose in the same week cannot tell which one a bad answer belongs
    to: those rises are unread for both.
    """
    declared = [lineage for lineage in book.progression.lineages
                if lineage.family in RUN_FAMILIES]
    if not declared:
        return []
    families = default()
    rows = ledger.parse_ledger(store)
    zones = [zone.slug for zone in ledger.pain_zones(store)]
    config = expected_rpe.settings()
    history = expected_rpe.history(store, rows, config) if config is not None else None
    plans = Plans(store)
    ran = {lineage.slug: runs(store, rows, lineage, families[lineage.family], plans, zones,
                              history) for lineage in declared}
    risen = {lineage.slug: [exposure.on for exposure in rises(
        families[lineage.family], [exposure for _, exposure in ran[lineage.slug]])]
        for lineage in declared}
    predictions = {row["prediction_key"]: row for row in store.prediction_rows()}
    found = []
    for lineage in declared:
        family = families[lineage.family]
        others = {iso_week.of(day) for slug, days in risen.items() if slug != lineage.slug
                  for day in days}
        confounded = frozenset(day for day in risen[lineage.slug] if iso_week.of(day) in others)
        overrides = tuple(
            Override(exposure.on, OverrideReason(session["override_reason"]),
                     session["override_prediction"],
                     outcome(family, lineage, exposure, confounded),
                     _borne_out(predictions.get(session["override_prediction"])))
            for session, exposure in ran[lineage.slug]
            if session.get("override_reason") is not None)
        found.append(Review(lineage.slug, tally(family, lineage,
                                                [exposure for _, exposure in ran[lineage.slug]],
                                                confounded), overrides))
    return found


def _days(days: Sequence[dt.date]) -> str:
    return ", ".join(f"{day:%d/%m}" for day in days)


def render_reviews(found: Sequence[Review]) -> list[str]:
    """The tally of the run lineages as the coach reads it; nothing without a lineage."""
    if not found:
        return []
    lines = [text.TALLY_HEADER]
    for review in found:
        counted = [(kind, days) for kind, days in (
            (RiseOutcome.TOLERATED, review.tally.tolerated),
            (RiseOutcome.FAILED, review.tally.failed),
            (RiseOutcome.HARMED, review.tally.harmed),
            (RiseOutcome.UNREAD, review.tally.unread)) if days]
        if not counted:
            lines.append(text.TALLY_NO_RISE.format(lineage=review.lineage))
        else:
            suggestion = review.tally.suggestion
            read = sum(len(days) for kind, days in counted if kind != RiseOutcome.UNREAD)
            lines.append(text.TALLY_LINE.format(
                lineage=review.lineage,
                counts=" ; ".join(text.TALLY_COUNT.format(
                    label=text.TALLY_OUTCOMES[kind], count=len(days), days=_days(days))
                    for kind, days in counted),
                suggestion=(text.TALLY_TOO_FEW.format(read=read, minimum=TALLY_MIN_RISES)
                            if suggestion is None else text.TALLY_SUGGESTIONS[suggestion])))
        lines.extend(text.OVERRIDE.format(
            day=item.on, reason=text.OVERRIDE_REASONS[item.reason],
            followed=text.OVERRIDE_FOLLOWED[item.followed],
            prediction=text.PREDICTION_OPEN if item.borne_out is None
            else text.PREDICTION_BORNE_OUT if item.borne_out else text.PREDICTION_REFUTED)
            for item in review.overrides)
    return lines
