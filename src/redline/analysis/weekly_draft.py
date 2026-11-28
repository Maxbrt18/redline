"""The draft of a week: the revision the engine writes from the cycle version's week,
before the coach amends it.

`draft` writes it from a `Frame`, arguments only; `frame` reads the frame from the store.
The draft is a whole revision (`weekly_plan.REVISION_SCHEMA`), from arguments only:
- each key session of the week, linked by its position, at the trame's dose, or at the
  lineage's when the lineage stands lower (a veto, a step back), never above;
- the long run at the week's long run; the race itself in the race's week;
- easy runs that bring the week to its volume, as many as the week before had;
- the strength sessions of the week before, on the same weekdays;
- every session placed on the athlete's days by the reorganisation's search
  (`redline.analysis.reorganize`): the same filters, the same ranking. A session the days
  cannot hold is left out, an easy run first.

A key session's distance is estimated: its work and recoveries at the easy pace, plus a
warm-up and a cool-down. The draft carries no decision: the coach's revision says what
settled it. With one, the draft always passes the gate: that is a tested invariant.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import plan as text
from redline.coach.fr import session_labels
from redline.model import iso_week, paths, rulebook, session_kinds, week_days
from redline.model import program as strength_program
from redline.model import race as race_model
from redline.model.notes import NoteError
from redline.model.plan_generator import Key, Week, work
from redline.model.program import Program
from redline.model.progression import Dose
from redline.model.vocabulary import KeyRole, SessionPriority, SessionStatus
from redline.model.week_days import Request
from redline.model.weekly_plan import active

from . import cycle_inputs, reorganize

#: A key session's warm-up and cool-down, in km.
AROUND_KEY_KM = 4.0
#: The pace a key session's distance is estimated at without an easy pace, in s/km.
DEFAULT_PACE_S = 360.0
#: The shortest easy run the draft writes, in km.
EASY_MIN_KM = 4.0
#: The weekdays sessions start on before the search places them (0 = Monday).
KEY_DAYS = (1, 3, 5)
EASY_DAYS = (0, 2, 4, 5, 1, 3)
LONG_DAY = 6
#: What a drafted run comes from: the cycle version, which `current-plan.md` projects.
SOURCES = ("[[current-plan]]",)


@dataclass(frozen=True)
class Frame:
    """Everything a week's draft is written from."""

    week: str
    bound: Week
    keys: tuple[Key, ...]
    #: The dose each run lineage stands at, by workout.
    lineages: Mapping[str, Dose]
    easy_pace_s: float | None
    #: The easy runs the week before had: the draft writes as many.
    easy_runs: int
    #: The strength sessions of the week before, as written.
    strength: tuple[dict, ...]
    request: Request
    rules: reorganize.Rules
    program: Program | None
    #: The race of the week: its day, its distance, its name.
    race: tuple[dt.date, float, str] | None = None


def _dose(key: Key, lineages: Mapping[str, Dose]) -> Dose | None:
    """The trame's dose, or the lineage's when it stands lower."""
    lineage = lineages.get(key.workout)
    if key.dose is None or lineage is None or work(lineage) >= work(key.dose):
        return key.dose
    return lineage


def _key_km(dose: Dose | None, pace_s: float) -> float:
    if dose is None:
        return AROUND_KEY_KM
    reps = dose.get("reps") or 1
    seconds = reps * (dose.get("rep_seconds") or 0) + (reps - 1) * (dose.get("recovery_seconds") or 0)
    return round(AROUND_KEY_KM + seconds / pace_s, 1)


def _int(value: float | None) -> int | None:
    return None if value is None else round(value)


def _day(week: str, weekday: int) -> str:
    return (iso_week.monday(week) + dt.timedelta(days=weekday)).isoformat()


def _run(identifier: str, title: str, workout: str, date: str, km: float, *,
         demanding: bool, priority: SessionPriority) -> dict:
    return {"id": identifier, "title": title, "date": date, "kind": session_kinds.family(workout),
            "workout": workout, "status": SessionStatus.PLANNED, "distance_target_km": km,
            "demanding": demanding, "priority": priority, "sources": list(SOURCES)}


def _sessions(frame: Frame) -> list[dict]:
    prefix = frame.week.lower()
    pace = frame.easy_pace_s or DEFAULT_PACE_S
    sessions: list[dict] = []
    for index, key in enumerate(frame.keys):
        day = _day(frame.week, KEY_DAYS[index % len(KEY_DAYS)])
        if key.role == KeyRole.TEST:
            sessions.append({**_run(f"{prefix}-test", text.DRAFT_TEST, "test", day, AROUND_KEY_KM,
                                    demanding=session_kinds.BY_SLUG["test"].demanding,
                                    priority=SessionPriority.KEY),
                             "key_position": key.position})
            continue
        dose = _dose(key, frame.lineages)
        session = _run(f"{prefix}-key-{key.position}", session_labels.name(key.workout),
                       key.workout, day, _key_km(dose, pace), demanding=True,
                       priority=SessionPriority.KEY)
        session["key_position"] = key.position
        if dose is not None:
            for name in ("reps", "rep_seconds", "recovery_seconds"):
                if dose.get(name) is not None:
                    session[name] = _int(dose.get(name))
        sessions.append(session)
    if frame.race is not None:
        on, km, name = frame.race
        sessions.append(_run(f"{prefix}-race", name, session_kinds.RACE, on.isoformat(), km,
                             demanding=True, priority=SessionPriority.KEY))
    elif frame.bound.long_run_km > 0:
        sessions.append(_run(f"{prefix}-long", session_labels.name(session_kinds.LONG),
                             session_kinds.LONG, _day(frame.week, LONG_DAY),
                             frame.bound.long_run_km, demanding=True,
                             priority=SessionPriority.KEY))
    left = frame.bound.volume_km - sum(session["distance_target_km"] for session in sessions
                                       if session["date"] <= _day(frame.week, 6))
    count = min(max(frame.easy_runs, 1), len(EASY_DAYS))
    while count > 1 and left / count < EASY_MIN_KM:
        count -= 1
    if left >= EASY_MIN_KM:
        each = round(left / count * 2) / 2
        for index in range(count):
            sessions.append(_run(f"{prefix}-easy-{index + 1}", session_labels.name(session_kinds.EASY),
                                 session_kinds.EASY, _day(frame.week, EASY_DAYS[index]), each,
                                 demanding=False, priority=SessionPriority.SUPPORT))
    for session in frame.strength:
        moved = dt.date.fromisoformat(session["date"]) + dt.timedelta(days=7)
        sessions.append({key: value for key, value in session.items()
                         if key != "db_id"}
                        | {"id": f"{prefix}-{session['id'].split('-', 1)[-1]}",
                           "date": moved.isoformat(), "status": SessionStatus.PLANNED})
    return sessions


def _placed(frame: Frame, sessions: list[dict]) -> list[dict]:
    """The sessions on the athlete's days, as the reorganisation's best variant puts
    them; the ones it leaves out are dropped."""
    found = reorganize.reorganise(reorganize.items(sessions, [], frame.program),
                                  iso_week.dates(frame.week), iso_week.monday(frame.week),
                                  frame.request, frame.rules)
    if not found.variants:
        return sessions
    best = found.variants[0]
    placement, dropped = dict(best.placement), set(best.dropped)
    return [{**session, "date": placement[session["id"]].isoformat()}
            if session["id"] in placement else session
            for session in sessions if session["id"] not in dropped]


def draft(frame: Frame, stamp: str) -> dict:
    """The week's draft, as a first revision written at `stamp`."""
    sessions = sorted(_placed(frame, _sessions(frame)), key=lambda session: (session["date"],
                                                                              session["id"]))
    runs = [session for session in sessions if session["kind"] == "run"]
    volume = round(min(frame.bound.volume_km,
                       sum(session.get("distance_target_km") or 0 for session in runs)), 1)
    evidence = text.DRAFT_EVIDENCE.format(week=frame.week)
    revision: dict = {
        "revision": 1, "computed_at": stamp, "reason": text.DRAFT_REASON.format(week=frame.week),
        "evidence": [evidence], "constraints": [], "sessions": sessions,
    }
    if runs:
        revision["volume_target_km"] = max(volume, 1.0)
    return revision


#: The easy runs a week has when the week before says nothing.
EASY_RUNS = 2


def _previous(store: Any, week: str) -> list[dict]:
    """The live sessions of the week before's active revision."""
    plan = store.load_plan(iso_week.shift(week, -1))
    if plan is None:
        return []
    return [session for session in active(plan)["sessions"]
            if session.get("status") != SessionStatus.CANCELLED]


def _program() -> Program | None:
    try:
        return strength_program.load(paths.vault_root())
    except NoteError:
        return None


def frame(store: Any, week: str, today: dt.date) -> Frame | None:
    """The frame of `week`'s draft, read from the store; None when no cycle version holds
    the week."""
    version = store.cycle_for_week(week)
    bound = version.week(week) if version is not None else None
    if version is None or bound is None:
        return None
    book = rulebook.current()
    parameters = book.parameters
    before = _previous(store, week)
    easy = [session for session in before if session.get("workout") == session_kinds.EASY]
    dates = iso_week.dates(week)
    request = week_days.effective(week, store.habits(dates[0], dates[-1]),
                                  store.stated_days(dates[0], dates[-1]),
                                  week_days.Request({}, {}))
    goal = race_model.active_goal(store)
    race = None
    if goal is not None and iso_week.of(goal.on) == week:
        distance = goal.distance_km or (goal.goal.distance_km if goal.goal else 0.0)
        race = (goal.on, float(distance), goal.name or session_labels.name(session_kinds.RACE))
    return Frame(
        week, bound, tuple(version.keys_of(week)),
        dict(cycle_inputs.lineages(store, book, today)), version.trame.hypotheses.easy_pace_s,
        len(easy) or EASY_RUNS,
        tuple(session for session in before if session.get("kind") == session_kinds.STRENGTH),
        request,
        reorganize.Rules(runs_per_day=parameters.runs_per_day,
                         sessions_per_day=parameters.sessions_per_day,
                         load_margin_pct=parameters.reorganize_load_margin_pct,
                         long_run_weekdays=parameters.long_run_weekdays),
        _program(), race)
