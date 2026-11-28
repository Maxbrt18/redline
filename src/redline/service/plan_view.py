"""The Plan screen of the app, and the proposal it lets the athlete take.

The cycle in force is shown whole, week by week: what each week may reach and its key
sessions, numbered as a session links to them, with today's intensities rendered from the
calibration in force. The week in progress and the two after it say whether their program
is written, which the Week screen opens; beyond, the trame alone. A proposal still open is
shown whole too, against the cycle it would replace, with the options the coach set aside.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from redline.analysis import cycle_report
from redline.model import intensity, iso_week, transitions
from redline.model import notebook_views as views
from redline.model import race as race_model
from redline.model.cycles import CycleVersion
from redline.model.plan_generator import Key, Trame, Week
from redline.model.vocabulary import CycleKind, KeyRole

#: The weeks from the current one whose written program the screen points to.
DETAILED_WEEKS = 3
#: What follows each kind of cycle.
NEXT = {CycleKind.PREPARATION: "recovery", CycleKind.GENERAL: "block",
        CycleKind.RECOVERY: "general", CycleKind.REBUILD: "general"}


def _slug(value: str) -> Any:
    """A vocabulary's member as the view's literal carries it."""
    return str(value)


def _int(value: float | None) -> int | None:
    return None if value is None else round(value)


def _key(key: Key, refs: intensity.References) -> views.PlanKey:
    pace = rpe = None
    if key.intensity is not None:
        rendered = intensity.render(key.intensity.ref, key.intensity.band, refs)
        if rendered.pace_s is not None:
            pace = [round(rendered.pace_s.low, 1), round(rendered.pace_s.high, 1)]
        elif rendered.rpe is not None:
            rpe = [rendered.rpe.low, rendered.rpe.high]
    dose = key.dose
    return {"position": key.position, "workout": key.workout, "role": _slug(key.role),
            "reps": _int(dose.get("reps")) if dose else None,
            "rep_seconds": _int(dose.get("rep_seconds")) if dose else None,
            "recovery_seconds": _int(dose.get("recovery_seconds")) if dose else None,
            "pace_s": pace, "rpe": rpe,
            "transition": transitions.label(key.transition) if key.transition else None}


def _week(week: Week, trame: Trame, refs: intensity.References, current: str,
          written: set[str]) -> views.PlanWeek:
    state = "done" if week.label < current else "current" if week.label == current else "upcoming"
    return {"week": week.label, "monday": iso_week.monday(week.label).isoformat(),
            "phase": _slug(week.phase), "deload": week.deload,
            "volume_km": week.volume_km, "long_run_km": week.long_run_km,
            "qualities": week.qualities,
            "segment_km": week.segment.km if week.segment is not None else None,
            "keys": [_key(key, refs) for key in trame.keys if key.week == week.label],
            "state": _slug(state), "written": week.label in written}


def _risks(trame: Trame) -> list[views.PlanRisk]:
    return [{"kind": _slug(risk.kind), "value": risk.value,
             "reference": risk.reference, "workout": risk.workout,
             "phase": str(risk.phase) if risk.phase is not None else None}
            for risk in trame.risks]


def _race(ops: Any, key: str | None) -> views.PlanRace | None:
    race = next((race for race in race_model.typed(ops) if race.key == key), None) if key else None
    return None if race is None else {"name": race.name, "date": race.on.isoformat()}


def _weeks(ops: Any, version: CycleVersion, today: dt.date) -> list[views.PlanWeek]:
    current = iso_week.of(today)
    near = {iso_week.shift(current, offset) for offset in range(DETAILED_WEEKS)}
    written = {label for label in near if ops.load_plan(label) is not None}
    goal = race_model.active_goal(ops)
    refs = cycle_report.references(ops, goal.goal if goal is not None else None)
    return [_week(week, version.trame, refs, current, written) for week in version.trame.weeks]


def view(ops: Any, today: dt.date, now: dt.datetime) -> views.PlanView:
    """The Plan screen: the cycle in force and the proposal still open."""
    version = ops.active_cycle()
    cycle: views.PlanCycle | None = None
    if version is not None:
        cycle = {"kind": _slug(version.kind),
                 "level": _slug(version.level) if version.level else None,
                 "version": version.version, "candidate": version.trame.candidate,
                 "race": _race(ops, version.race_key), "weeks": _weeks(ops, version, today),
                 "risks": _risks(version.trame), "next": _slug(NEXT[version.kind])}
    proposal = ops.open_cycle_proposal(now)
    summary: views.PlanProposalSummary | None = None
    if proposal is not None:
        summary = {"id": str(proposal.id), "race": _race(ops, proposal.race_key),
                   "weeks": len(proposal.trame.weeks),
                   "starts_on": iso_week.monday(proposal.trame.weeks[0].label).isoformat(),
                   "peak_km": max(week.volume_km for week in proposal.trame.weeks),
                   "expires_on": (proposal.expires_on or today).isoformat()}
    return {"current_week": iso_week.of(today), "cycle": cycle, "proposal": summary}


def proposal_view(ops: Any, today: dt.date, now: dt.datetime) -> views.CycleProposalView | None:
    """The proposal still open, whole, against the cycle in force; None without one."""
    proposal = ops.open_cycle_proposal(now)
    if proposal is None:
        return None
    active = ops.active_cycle()
    trame = proposal.trame
    decision = ops.cycle_decision(proposal.id) or {}
    return {
        "id": str(proposal.id), "candidate": trame.candidate,
        "race": _race(ops, proposal.race_key), "weeks": _weeks(ops, proposal, today),
        "peak_km": max(week.volume_km for week in trame.weeks),
        "long_run_km": max(week.long_run_km for week in trame.weeks),
        "flagships": sum(1 for key in trame.keys if key.role == KeyRole.FLAGSHIP),
        "risks": _risks(trame),
        "current": {
            "kind": _slug(active.kind) if active is not None else None,
            "volume_km": (max(week.volume_km for week in active.trame.weeks)
                          if active is not None else None),
            "qualities": (max(week.qualities for week in active.trame.weeks)
                          if active is not None else None)},
        "alternatives": [{"option": str(item.get("option") or ""),
                          "rejected_because": str(item.get("rejected_because") or "")}
                         for item in decision.get("alternatives") or []],
        "proposed_on": (proposal.created_on or today).isoformat(),
        "expires_on": (proposal.expires_on or today).isoformat(),
    }


def take(ops: Any, version_id: str, now: dt.datetime) -> bool:
    """The athlete takes the proposal from the app; False when it is no longer open."""
    with ops.database.transaction() as connection:
        return ops.take_cycle_in(connection, uuid.UUID(version_id), now,
                                 actor="athlete") is not None
