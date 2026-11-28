"""Typed guardrail no. 8: a transition typed on a session, at most so many a week."""

import datetime as dt

import pytest

from redline.model import transitions, weekly_plan
from redline.model.schema import SchemaError

RACE = dt.date(2026, 11, 26)
TAPER = ("2026-W46", "2026-W47", "2026-W48")


def _session(identifier, day, kind=None, status="planned", workout="easy", **extra):
    payload = {"id": identifier, "date": day, "status": status, "workout": workout,
               "kind": "run", **extra}
    if kind:
        payload["transition"] = kind
    return payload


# ------------------------------------------------------------------ transitions

def test_a_missed_transition_opens_nothing_but_a_done_or_future_one_does():
    sessions = [
        _session("faite", "2026-09-22", "terrain", status="done"),
        _session("manquee", "2026-09-24", "night"),
        _session("annulee", "2026-09-30", "descent", status="cancelled"),
        _session("a-venir", "2026-10-03", "quality"),
    ]
    found = transitions.opened(sessions, dt.date(2026, 10, 1))
    assert [t.session_id for t in found] == ["faite", "a-venir"]


def test_a_turn_command_carries_dates_not_text():
    """The `stage_command` path returns `date` values: a revision once crashed on them."""
    sessions = [_session("a-venir", dt.date(2026, 10, 3), "quality")]
    assert [t.day for t in transitions.opened(sessions, dt.date(2026, 10, 1))] == [
        dt.date(2026, 10, 3)]


def test_two_transitions_in_one_week_are_refused():
    sessions = [_session("a", "2026-10-06", "night"),
                _session("b", "2026-10-08", "descent")]
    assert transitions.per_week_errors(sessions, "W41")
    sessions[1]["status"] = "cancelled"
    assert transitions.per_week_errors(sessions, "W41") == []


def test_the_plan_schema_types_the_transition():
    from test_weekly_plan import plan, session

    with pytest.raises(SchemaError, match="transition"):
        weekly_plan.validate(plan([session(transition="vma")]))
    weekly_plan.validate(plan([session(transition="night")]))
    two = [session("w32-mar-ef", "2026-08-04", transition="night"),
           session("w32-jeu-ef", "2026-08-06", transition="terrain")]
    with pytest.raises(SchemaError, match="transitions in the week"):
        weekly_plan.validate(plan(two))


# ------------------------------------------------------------------ solver
