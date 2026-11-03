"""Reorganising a week from the athlete's days: the variants, the screen, and the
revision a chosen variant becomes.

The variants are computed again when one is taken: the page names a variant by its rank
for these days and this revision, never by a placement it could have altered.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Literal

from redline.analysis import reorganize
from redline.coach import dates
from redline.coach.fr import reorganize as text
from redline.coach.fr import session_labels
from redline.model import notebook_views as views
from redline.model import paths, rulebook, week_days
from redline.model import program as strength_program
from redline.model.errors import InputError
from redline.model.notes import NoteError
from redline.model.weekly_plan import SETTLED_STATUSES, active, blocking_gates, validate

from . import plan_revision

ACTOR = "notebook"


class ReorganizeError(InputError):
    """The variant cannot be taken: the week changed, or the choice is gone."""


def request_of(store, week: str, payload: object) -> week_days.Request:
    """The days as they stand: what the page sends, over what was stated, over habits."""
    sent = week_days.parse(payload, week)
    dates = week_days.week_dates(week)
    return week_days.effective(week, store.habits(dates[0], dates[-1]),
                               store.stated_days(dates[0], dates[-1]), sent)


@dataclass(frozen=True)
class Computation:
    week: str
    revision: dict
    request: week_days.Request
    items: list[reorganize.Item]
    outcome: reorganize.Outcome


def pain_signal(store, today: dt.date) -> bool:
    """An effort pain above the rulebook's cap (rule n3) in the last 7 days."""
    from redline.analysis import decision_state, ledger
    from redline.model import ledger as ledger_model
    cap = rulebook.current().parameters.pain_cap
    since = today - dt.timedelta(days=decision_state.PAIN_WINDOW_DAYS)
    return any(value > cap for row in ledger.parse_ledger(store)
               if since < row.date <= today and row.type in ledger_model.EFFORT_TYPES
               for value in row.pains.values())


def _program() -> tuple[strength_program.Program | None, dict]:
    vault = paths.vault_root()
    try:
        return strength_program.load(vault), strength_program.rpe_targets(vault)
    except NoteError:
        return None, {}


def compute(store, plan: dict, week: str, request: week_days.Request, today: dt.date,
            signal: bool) -> Computation:
    """The variants of the active revision of `plan` for the days of `request`."""
    revision = active(plan)
    program, targets = _program()
    parameters = rulebook.current().parameters
    sessions = revision["sessions"]
    items = reorganize.items(sessions, revision.get("gates") or [], program, targets)
    outcome = reorganize.reorganise(
        items, week_days.week_dates(week), today, request,
        reorganize.Rules(runs_per_day=parameters.runs_per_day,
                         sessions_per_day=parameters.sessions_per_day,
                         load_margin_pct=parameters.reorganize_load_margin_pct,
                         long_run_weekdays=parameters.long_run_weekdays,
                         pain_signal=signal),
        rest_days=frozenset(dt.date.fromisoformat(session["date"]) for session in sessions
                            if session["workout"] == "rest"),
        settled=frozenset(session["id"] for session in sessions
                          if session["status"] in SETTLED_STATUSES))
    return Computation(week, revision, request, items, outcome)


Blocked = Literal["awaiting", "missing", "closed"]


def _blocked(plan: dict | None, week: str, today: dt.date) -> Blocked | None:
    if week_days.week_dates(week)[-1] < today:
        return "closed"
    if plan is None:
        return None
    revision = active(plan)
    if blocking_gates(plan, today) or any(
            dt.date.fromisoformat(gate["due"]) < today for gate in revision.get("gates") or []):
        return "awaiting"
    return None


# ------------------------------------------------------------------ the screen

def _figure(session: dict) -> str:
    if session.get("distance_target_km"):
        return f"{session['distance_target_km']:g} km".replace(".", ",")
    if session.get("duration_cap_min"):
        return f"≤{session['duration_cap_min']} min"
    if session.get("distance_cap_km"):
        return f"≤{session['distance_cap_km']:g} km".replace(".", ",")
    return ""


def _state(day: week_days.Day | None) -> views.DayState | None:
    if day is None:
        return None
    row = week_days.state_row(day)
    return {"kind": row["kind"], "minutes": row["minutes"], "pace": row["pace"],
            "required": row["required"], "sport": row["sport"], "demanding": row["demanding"],
            "run_too": row["run_too"], "habit": day.habit}


def _days(revision: dict | None, week: str, today: dt.date, request: week_days.Request,
          habits: list[dict]) -> list[views.ReorganizeDay]:
    out: list[views.ReorganizeDay] = []
    for date in week_days.week_dates(week):
        iso = date.isoformat()
        here = [session for session in (revision or {}).get("sessions", [])
                if session["date"] == iso and session["status"] != "cancelled"]
        habit = week_days.habit_on(habits, date)
        out.append({
            "date": iso, "past": date < today,
            "stated": _state(request.days.get(date)),
            "habit_id": str(habit["id"]) if habit else None,
            "rest": any(session["workout"] == "rest" for session in here),
            "sessions": [{"id": session["id"], "title": session["title"],
                          "kind": session["kind"], "workout": session["workout"],
                          "figure": _figure(session)}
                         for session in here if session["workout"] != "rest"],
        })
    return out


def _variant(variant: reorganize.Variant, computation: Computation) -> views.Variant:
    by_id = {item.id: item for item in computation.items}
    sessions = {session["id"]: session for session in computation.revision["sessions"]}
    placed = dict(variant.placement)
    moved = {move.after for move in variant.moves}
    carried_on = placed.get(variant.carried) if variant.carried else None
    days: list[views.VariantDay] = []
    for date in week_days.week_dates(computation.week):
        here = sorted((item_id for item_id, at in placed.items() if at == date),
                      key=lambda item_id: (by_id[item_id].strength, item_id))
        days.append({
            "date": date.isoformat(),
            "labels": list(dict.fromkeys(session_labels.short(by_id[item_id].workout)
                                         for item_id in here)),
            "load": round(sum(by_id[item_id].load for item_id in here), 1),
            "moved": date in moved, "carried": date == carried_on,
        })
    return {
        "moves": [{"session": move.session, "title": sessions[move.session]["title"],
                   "before": move.before.isoformat(), "after": move.after.isoformat()}
                  for move in variant.moves],
        "dropped": [{"session": item_id, "title": sessions[item_id]["title"],
                     "km": sessions[item_id].get("distance_target_km")
                     or sessions[item_id].get("distance_cap_km")}
                    for item_id in variant.dropped],
        "notes": [{"remark": note.remark.value,
                   "title": sessions[note.session]["title"] if note.session else None,
                   "date": note.date.isoformat() if note.date else None,
                   "value": note.value} for note in variant.notes],
        "days": days,
        "around_min": variant.around_min,
    }


def view(store, week: str, payload: object, today: dt.date) -> views.ReorganizeView:
    request = request_of(store, week, payload)
    dates = week_days.week_dates(week)
    habits = store.habits(dates[0], dates[-1])
    plan = store.load_plan(week)
    if plan is not None:
        validate(plan)
    blocked = _blocked(plan, week, today)
    revision = active(plan) if plan is not None else None
    if revision is None or blocked:
        return {"week": week, "revision": revision["revision"] if revision else 0,
                "written": revision is not None, "blocked": blocked,
                "days": _days(revision, week, today, request, habits), "variants": [],
                "others": 0, "held_back": []}
    computation = compute(store, plan, week, request, today, pain_signal(store, today))
    titles = {session["id"]: session["title"] for session in revision["sessions"]}
    return {
        "week": week, "revision": revision["revision"], "written": True, "blocked": None,
        "days": _days(revision, week, today, request, habits),
        "variants": [_variant(variant, computation) for variant in computation.outcome.variants],
        "others": computation.outcome.others,
        "held_back": [titles[item] for item in computation.outcome.held_back],
    }


# ------------------------------------------------------------------ taking a variant

def _day(date: dt.date) -> str:
    return f"{dates.weekday(date, 'abbreviated')} {date:%d/%m}"


def _summary(variant: reorganize.Variant, titles: dict[str, str]) -> str:
    parts = [text.MOVED.format(title=titles[move.session], before=_day(move.before),
                               after=_day(move.after)) for move in variant.moves]
    parts += [text.DROPPED.format(title=titles[item]) for item in variant.dropped]
    return " ; ".join(parts) or text.UNCHANGED


def _words(day: week_days.Day) -> str:
    return text.DAY_STATES[day.kind.value].format(
        minutes=day.minutes, pace=text.PACES.get(day.pace.value if day.pace else "", ""),
        firmness=text.FIRMNESS[day.required], sport=day.sport,
        effort=text.EFFORT[day.demanding], run=text.RUN_TOO[day.run_too])


def _stated(request: week_days.Request, titles: dict[str, str]) -> str:
    said = [text.DAY.format(day=_day(date), state=_words(day))
            for date, day in sorted(request.days.items())]
    said += [text.PINNED.format(title=titles.get(session, session), day=_day(date))
             for session, date in sorted(request.pins.items())]
    return " ; ".join(said) or text.NO_DAY


def revision_of(computation: Computation, choice: int, stamp: str) -> dict:
    """The revision the chosen variant becomes: the same sessions, their new dates, the
    dropped ones cancelled at the athlete's request, and the variants set aside."""
    variants = computation.outcome.variants
    variant = variants[choice]
    previous = computation.revision
    titles = {session["id"]: session["title"] for session in previous["sessions"]}
    evidence = text.EVIDENCE.format(stamp=stamp, days=_stated(computation.request, titles))
    placed = dict(variant.placement)
    dropped = set(variant.dropped)
    sessions = []
    for session in previous["sessions"]:
        copy = {key: value for key, value in session.items() if key != "db_id"}
        # The read model shows what happened as `done`; a revision states intent only.
        if copy["status"] not in {"planned", "cancelled"}:
            copy["status"] = "planned"
        if session["id"] in placed:
            copy["date"] = placed[session["id"]].isoformat()
        if session["id"] in dropped:
            copy["status"] = "cancelled"
        sessions.append(copy)
    number, count = choice + 1, len(variants)
    changes: list[dict] = [{"session_id": move.session, "fields": ["date"], "trigger": evidence,
                            "context": text.CHANGE_CONTEXT.format(number=number, count=count),
                            "rule": text.CHANGE_RULE} for move in variant.moves]
    changes += [{"session_id": item, "fields": ["status"], "trigger": evidence,
                 "context": text.DROP_CONTEXT, "rule": text.CHANGE_RULE,
                 "restriction": {"rule": "request", "fact": evidence}}
                for item in variant.dropped]
    lost = sum((session.get("distance_target_km") or session.get("distance_cap_km") or 0)
               for session in previous["sessions"] if session["id"] in dropped)
    revision: dict = {
        "revision": previous["revision"] + 1,
        "computed_at": stamp,
        "reason": text.REASON.format(number=number, count=count,
                                     summary=_summary(variant, titles)),
        "evidence": [evidence],
        "constraints": list(previous.get("constraints") or []),
        "changes": changes,
        "sessions": sessions,
        "decision": {"by": "request", "alternatives": [
            {"option": text.ALTERNATIVE.format(number=index + 1,
                                               summary=_summary(other, titles)),
             "rejected_because": text.REJECTED}
            for index, other in enumerate(variants) if index != choice]},
    }
    if previous.get("gates"):
        revision["gates"] = previous["gates"]
    if previous.get("volume_target_km") is not None:
        revision["volume_target_km"] = round(max(1.0, previous["volume_target_km"] - lost), 1)
    return revision


def take(store, week: str, payload: object, expected: int, choice: int, today: dt.date,
         stamp: str, warn=lambda line: None) -> int:
    """Write the chosen variant as the next revision; its number."""
    request = request_of(store, week, payload)
    plan = store.load_plan(week)
    if plan is not None:
        validate(plan)
    if _blocked(plan, week, today) or plan is None:
        raise ReorganizeError(f"{week}: nothing can move this week")
    if active(plan)["revision"] != expected:
        raise ReorganizeError(f"{week}: revision {expected} is no longer the active one")
    computation = compute(store, plan, week, request, today, pain_signal(store, today))
    if not 0 <= choice < len(computation.outcome.variants):
        raise ReorganizeError(f"{week}: variant {choice + 1} does not exist for these days")
    variant = computation.outcome.variants[choice]
    if not variant.moves and not variant.dropped:
        raise ReorganizeError(f"{week}: variant {choice + 1} changes nothing")
    revision = revision_of(computation, choice, stamp)
    _, current = plan_revision.checked(store, week, revision, plan, stamp=stamp, warn=warn)
    store.append_plan_revision(week, current, actor=ACTOR)
    return current["revision"]


# ------------------------------------------------------------------ days stated ahead, habits

def state_days(store, week: str, payload: object, today: dt.date) -> None:
    """Keep the days stated for `week` from today on: the coach writes the week with them."""
    dates = week_days.week_dates(week)
    if dates[-1] < today:
        raise ReorganizeError(f"{week}: the week is over")
    sent = week_days.parse(payload, week)
    store.state_days(max(dates[0], today), dates[-1],
                     [{"day": date, **week_days.state_row(day)}
                      for date, day in sorted(sent.days.items()) if date >= today])


def _habit(row: dict) -> views.Habit:
    state = week_days.state_row(week_days.from_row(row["starts_on"], row, habit=True))
    return {"kind": state["kind"], "minutes": state["minutes"], "pace": state["pace"],
            "required": state["required"], "sport": state["sport"],
            "demanding": state["demanding"], "run_too": state["run_too"], "habit": True,
            "id": str(row["id"]), "weekday": row["weekday"],
            "starts_on": row["starts_on"].isoformat(),
            "ends_on": row["ends_on"].isoformat() if row["ends_on"] else None}


def habits_view(store, today: dt.date) -> views.HabitsView:
    """The habits in force or to come."""
    return {"habits": [_habit(row) for row in store.habits(today)]}


def add_habit(store, payload: object, today: dt.date) -> None:
    """A habit from today on; the one it replaces on that weekday ends yesterday."""
    clean = week_days.HABIT_SCHEMA.validate(payload, "habit")
    day = week_days.from_row(today, clean)
    for row in store.habits(today):
        if row["weekday"] == clean["weekday"]:
            store.end_habit(row["id"], today - dt.timedelta(days=1))
    store.add_habit({"weekday": clean["weekday"], "starts_on": today, **week_days.state_row(day)})


def end_habit(store, habit_id: str, today: dt.date) -> None:
    if not store.end_habit(uuid.UUID(habit_id), today - dt.timedelta(days=1)):
        raise ReorganizeError(f"habit {habit_id}: unknown")


def days_readback(store, week: str) -> str:
    """The days known for `week`, in the coach's words."""
    request = request_of(store, week, {"days": []})
    if not request.days:
        return text.DAYS_NONE.format(week=week)
    return "\n".join([text.DAYS_HEADER.format(week=week), *(
        text.DAYS_LINE.format(day=_day(date), state=_words(day),
                              habit=text.HABIT if day.habit else "")
        for date, day in sorted(request.days.items()))])
