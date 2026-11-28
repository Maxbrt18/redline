"""Computed guardrail no. 8: typed transitions.

A transition is a session whose nature leaves the past weeks (a first quality, a first
descent block, new terrain): the program types it (`transition`), and a week carries at
most the rulebook's `transitions_per_week` (`per_week_errors`, called by revision
validation). The first times a race requires are placed by its preparation's trame
(`redline.model.plan_generator`), never by a solver of their own.

Easy increases (long-run step, longer easy run, extra outing) are not transitions: they
go through the observable gate (`rulebook.py`), read at the review.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from redline.coach.fr import transitions as text

from . import iso_week, rulebook
from .vocabulary import SessionStatus


def kinds() -> tuple[str, ...]:
    """The kinds a transition may carry: those of the instance rulebook."""
    return rulebook.current().kinds


def label(kind: str) -> str:
    return rulebook.current().label(kind)


def day_of(value: dt.date | str) -> dt.date:
    """A session date: ISO text in the JSON, `date` in a turn command."""
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)


@dataclass(frozen=True)
class Transition:
    kind: str
    day: dt.date
    session_id: str

    @property
    def week(self) -> str:
        return iso_week.of(self.day)

    def __str__(self) -> str:
        return text.SESSION_FIRST.format(label=label(self.kind), day=self.day, session_id=self.session_id)


def opened(sessions: list[dict], as_of: dt.date) -> list[Transition]:
    """The transitions of a revision that count: done, or still on the program.

    A past session that was not done (still `planned` after its date) did not happen;
    neither did a cancelled one.
    """
    found = []
    for session in sessions:
        kind = session.get("transition")
        if not kind or session.get("status") == SessionStatus.CANCELLED:
            continue
        day = day_of(session["date"])
        if session.get("status") == SessionStatus.DONE or day >= as_of:
            found.append(Transition(kind, day, session["id"]))
    return found


def per_week_errors(sessions: list[dict], where: str) -> list[str]:
    """A kind known to the rulebook, and no more transitions per week than it allows:
    a check internal to one revision."""
    book = rulebook.current()
    problems = [(f"{where}: session {session["id"]}, transition {session["transition"]!r} unknown to the rulebook "
                 f"(expected: {", ".join(book.kinds)})")
                for session in sessions
                if session.get("transition") and session["transition"] not in book.kinds]
    typed = [session for session in sessions
             if session.get("transition") and session.get("status") != SessionStatus.CANCELLED]
    allowed = book.parameters.transitions_per_week
    if len(typed) <= allowed:
        return problems
    names = ", ".join(f"{s['id']} ({s['transition']})" for s in typed)
    return [*problems, f"{where}: {len(typed)} transitions in the week ({names}), rule n8: {allowed} at most"]
