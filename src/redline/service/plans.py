"""Pure reading and rendering of the canonical weekly plan."""

from __future__ import annotations

import enum
import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from redline.coach import dates
from redline.coach.fr import plan as wording
from redline.model import weekly_plan as weekly_plan_model

#: A day that empties is named. The prescriptive render filters on `remaining()`,
#: which drops cancelled sessions: a day whose only session was removed would vanish
#: from the list instead of showing as empty, leaving a gap neither reader can see.
#: The render claims "here is what remains", so it must speak of EVERY day in the
#: period it announces, including to say there is nothing.
NOTHING = wording.MESSENGER_NOTHING


class PlanStatus(enum.Enum):
    VALID = "valid"
    #: A dated check awaits the athlete's answer.
    AWAITING = "awaiting"
    #: An unlinked activity appeared since the plan was computed: to reexamine.
    STALE = "stale"
    INVALID = "invalid"


def _monday(week: str) -> date:
    year, _, number = week.partition("-W")
    return date.fromisocalendar(int(year), int(number), 1)


def _sunday(day: date) -> date:
    return day + timedelta(days=6 - day.weekday())


def _revision(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def due_gates_by_kind(context: str, today: str) -> tuple[list, list]:
    """Return due blocking gates and due observation gates from loaded context."""
    revision = _revision(context)
    if revision is None:
        return [], []
    as_of = date.fromisoformat(today)
    return weekly_plan_model.split_by_when(
        weekly_plan_model.due_gates_of(revision, as_of))


def blocked_session_ids(context: str, today: str) -> frozenset[str]:
    """Return the session identifiers explicitly held by due preconditions."""
    blocking, _ = due_gates_by_kind(context, today)
    return frozenset(session_id for gate in blocking for session_id in gate["holds"])


def strength_due(context: str, today: str) -> dict | None:
    """Return today's unsettled strength session, if any."""
    revision = _revision(context)
    if revision is None:
        return None
    for session in revision.get("sessions") or []:
        if (isinstance(session, dict)
                and session.get("kind") == "strength"
                and session.get("date") == today
                and session.get("status") not in weekly_plan_model.SETTLED_STATUSES):
            return session
    return None


def _done(session: dict) -> str:
    return wording.MESSENGER_DONE if session.get("status") == "done" else ""


def _short(session: dict) -> str:
    distance = weekly_plan_model.distance_of(session)
    timing = (wording.MESSENGER_AT.format(time=session["start_time"])
              if session.get("start_time") else "")
    head = session["title"] + timing
    head = f"{head}, {distance}" if distance else head
    return head + _done(session)


def _full(session: dict) -> str:
    head = session["title"] + _done(session)
    if session.get("start_time"):
        head += wording.MESSENGER_AT.format(time=session["start_time"])
    details = weekly_plan_model.details_of(session)
    if session.get("location_label"):
        details.append(wording.MESSENGER_PLACE + session["location_label"])
    return ", ".join([head, *details])


def _label(day: date) -> str:
    return wording.MESSENGER_DAY.format(weekday=dates.weekday(day, "abbreviated"), day=day)


def _day_lines(sessions: list) -> list[str]:
    lines = []
    for date_text in sorted({session["date"] for session in sessions}):
        day = datetime.fromisoformat(date_text)
        same_day = [session for session in sessions if session["date"] == date_text]
        lines.append(f"{_label(day)} : "
                     + " + ".join(_short(session) for session in same_day))
    return lines


def _span(first: date, last: date) -> Iterable[date]:
    while first <= last:
        yield first
        first += timedelta(days=1)


def _covered_lines(shown: list, every: list, span: Iterable[date],
                   excluded: frozenset[str]) -> list[str]:
    """One line per day of the announced period, `RIEN` included.

    Two days stay silent, for the same reason: the message does not cover them. The
    day a dated check HOLDS, because the check's question goes out in the same turn
    and already says what is expected. The day whose session is DONE, because a day
    behind us has nothing left to prescribe. A cancelled day is named: it is exactly
    the gap the render must make visible.
    """
    by_day: dict[str, list] = {}
    for session in shown:
        by_day.setdefault(session["date"], []).append(session)
    held = {session["date"] for session in every if session.get("id") in excluded}
    facts = {session["date"] for session in every if session.get("status") == "done"}
    lines = []
    for day in span:
        stamp = day.isoformat()
        if stamp in by_day:
            lines.append(f"{_label(day)} : "
                         + " + ".join(_short(s) for s in by_day[stamp]))
        elif stamp not in held and stamp not in facts:
            lines.append(f"{_label(day)} : {NOTHING}")
    return lines


def prescription_lines(raw: str, since: str, *,
                       exclude_ids: frozenset[str] = frozenset()) -> list[str]:
    """Render the remaining prescription without its revision header."""
    revision = _revision(raw)
    if revision is None:
        return []
    sessions = weekly_plan_model.remaining(
        weekly_plan_model.scheduled(revision.get("sessions", [])), since=since)
    return _day_lines([session for session in sessions
                       if session.get("id") not in exclude_ids])


_PRESCRIPTION_FIELDS = (
    "title", "date", "start_time", "workout", "status", "distance_target_km", "distance_cap_km",
    "duration_cap_min", "rpe_min", "rpe_max", "hr_zone", "cadence_target",
    "demanding", "carbs_target_g_h", "location_label", "strength", "note",
)


def _signature(session: dict) -> tuple:
    return tuple(json.dumps(session.get(field), sort_keys=True, ensure_ascii=False)
                 for field in _PRESCRIPTION_FIELDS)


def prescription_state(raw: str, since: str, *,
                       exclude_ids: frozenset[str] = frozenset()) -> list[tuple]:
    """Full prescriptive state, distinct from the short render sent on Messenger."""
    revision = _revision(raw)
    if revision is None:
        return []
    sessions = weekly_plan_model.remaining(
        revision.get("sessions", []), since=since)
    return sorted(
        (session["id"], _signature(session))
        for session in sessions if session.get("id") not in exclude_ids)


def render_plan_for_scope(raw: str, week: str, scope: str, *, today: str,
                          exclude_ids: frozenset[str] = frozenset()) -> str:
    """Render exactly what the athlete asks for, including when they ask again."""
    if scope == "today":
        return render_plan_message(
            raw, week, today=today, exclude_ids=exclude_ids)
    if scope == "full":
        return render_plan_message(raw, week, exclude_ids=exclude_ids)
    if scope == "next":
        revision = _revision(raw)
        if revision is None:
            return ""
        candidates = weekly_plan_model.remaining(
            revision.get("sessions", []), since=today)
        candidates = [session for session in candidates
                      if session.get("id") not in exclude_ids]
        if not candidates:
            return wording.MESSENGER_NOTHING_UNTIL_SUNDAY
        first = min(session["date"] for session in candidates)
        same_day = [session for session in candidates if session["date"] == first]
        day = date.fromisoformat(first)
        return (wording.MESSENGER_NEXT.format(day=_label(day))
                + " ; ".join(_full(session) for session in same_day) + ".")
    # `remaining` is also the safe fallback for old clients that carried only a D.
    return render_plan_message(
        raw, week, since=today, exclude_ids=exclude_ids)


def render_plan_delta(before_raw: str, after_raw: str, week: str, *, today: str,
                      exclude_ids: frozenset[str] = frozenset()) -> str:
    """A reactive revision announces its changes, never the whole week."""
    before, after = _revision(before_raw), _revision(after_raw)
    if after is None:
        return ""
    old = {session["id"]: session for session in (before or {}).get("sessions", [])}
    new = {session["id"]: session for session in after.get("sessions", [])}
    changed: list[str] = []
    for session_id, session in new.items():
        previous = old.get(session_id)
        relevant_date = max(session.get("date", ""), (previous or {}).get("date", ""))
        if relevant_date < today or session_id in exclude_ids:
            continue
        if previous is not None and _signature(previous) == _signature(session):
            continue
        day = date.fromisoformat(session["date"])
        if session.get("status") == "cancelled":
            changed.append(wording.MESSENGER_CANCELLED.format(day=_label(day), title=session["title"]))
        elif previous is not None and previous.get("date") != session.get("date"):
            old_day = date.fromisoformat(previous["date"])
            changed.append(
                wording.MESSENGER_MOVED.format(title=session["title"], before=_label(old_day),
                                               after=_label(day))
                + ", ".join(weekly_plan_model.details_of(session)))
        else:
            changed.append(f"{_label(day)} : {_full(session)}")
    for session_id, session in old.items():
        if session_id in new or session.get("date", "") < today or session_id in exclude_ids:
            continue
        changed.append(wording.MESSENGER_REMOVED.format(
            day=_label(date.fromisoformat(session["date"])), title=session["title"]))
    if not changed:
        return ""
    # No revision number nor ISO week: the athlete reads a program, not a ledger.
    if len(changed) == 1:
        return wording.MESSENGER_ONE_CHANGE.format(change=changed[0])
    return "\n".join([wording.MESSENGER_CHANGES, *(f"{line}." for line in changed)])


def render_plan_message(raw: str, week: str, *, today: str = "", since: str = "",
                        exclude_ids: frozenset[str] = frozenset()) -> str:
    """Render one validated revision for Messenger."""
    revision = _revision(raw)
    if revision is None:
        return ""
    all_sessions = weekly_plan_model.scheduled(revision.get("sessions", []))
    held_sessions = [session for session in all_sessions
                     if session.get("id") in exclude_ids]
    sessions = all_sessions
    sessions = [session for session in sessions if session.get("id") not in exclude_ids]
    if today:
        sessions = [session for session in sessions if session.get("date") == today]
        held_today = [session for session in held_sessions
                      if session.get("date") == today]
    elif since:
        sessions = weekly_plan_model.remaining(sessions, since=since)
    if not sessions:
        if today and held_today:
            titles = " + ".join(dict.fromkeys(session["title"] for session in held_today))
            return wording.MESSENGER_TODAY_HELD.format(titles=titles)
        return (wording.MESSENGER_NOTHING_UNTIL_SUNDAY if since
                else wording.MESSENGER_NOTHING_TODAY)
    if today:
        text = wording.MESSENGER_TODAY + " ; ".join(_full(session) for session in sessions)
        return text if text.endswith((".", "!", "?")) else text + "."
    every = revision.get("sessions", [])
    first = date.fromisoformat(since) if since else _monday(week)
    header = (wording.MESSENGER_REST_OF_WEEK if since
              else wording.MESSENGER_WEEK.format(first=first, last=_sunday(first)))
    return "\n".join([header,
                       *_covered_lines(sessions, every,
                                       _span(first, _sunday(first)), exclude_ids)])
