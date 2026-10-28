"""Typed guardrail no. 8: transitions and backward feasibility, without a window."""

import datetime as dt
from types import SimpleNamespace

import pytest

from redline.analysis import feasibility
from redline.model import transitions, weekly_plan
from redline.model.schema import SchemaError
from redline.model.transitions import LONG_RUN_STEP, Problem, Rules

RACE = dt.date(2026, 11, 26)
TAPER = ("2026-W46", "2026-W47", "2026-W48")


def _session(identifier, day, kind=None, status="planned", workout="easy", **extra):
    payload = {"id": identifier, "date": day, "status": status, "workout": workout,
               "kind": "run", **extra}
    if kind:
        payload["transition"] = kind
    return payload


def _problem(**overrides):
    payload = {
        "race_on": RACE, "weeks": tuple(f"2026-W{n}" for n in range(40, 46)),
        "assimilation": frozenset({"2026-W43"}), "taper": TAPER,
        "fixed": {"2026-W40": (LONG_RUN_STEP,)},
        "required": ("nocturne", "descente", "reconnaissance"),
    }
    payload.update(overrides)
    return Problem(**payload)


# ------------------------------------------------------------------ transitions

def test_a_missed_transition_opens_nothing_but_a_done_or_future_one_does():
    sessions = [
        _session("faite", "2026-09-22", "terrain", status="done"),
        _session("manquee", "2026-09-24", "nocturne"),
        _session("annulee", "2026-09-30", "descente", status="cancelled"),
        _session("a-venir", "2026-10-03", "qualite"),
    ]
    found = transitions.opened(sessions, dt.date(2026, 10, 1))
    assert [t.session_id for t in found] == ["faite", "a-venir"]


def test_a_turn_command_carries_dates_not_text():
    """The `stage_command` path returns `date` values: a revision once crashed on them."""
    sessions = [_session("a-venir", dt.date(2026, 10, 3), "qualite")]
    assert [t.day for t in transitions.opened(sessions, dt.date(2026, 10, 1))] == [
        dt.date(2026, 10, 3)]


def test_two_transitions_in_one_week_are_refused():
    sessions = [_session("a", "2026-10-06", "nocturne"),
                _session("b", "2026-10-08", "descente")]
    assert transitions.per_week_errors(sessions, "W41")
    sessions[1]["status"] = "cancelled"
    assert transitions.per_week_errors(sessions, "W41") == []


def test_the_plan_schema_types_the_transition():
    from test_weekly_plan import plan, session

    with pytest.raises(SchemaError, match="transition"):
        weekly_plan.validate(plan([session(transition="vma")]))
    weekly_plan.validate(plan([session(transition="nocturne")]))
    two = [session("w32-mar-ef", "2026-08-04", transition="nocturne"),
           session("w32-jeu-ef", "2026-08-06", transition="terrain")]
    with pytest.raises(SchemaError, match="transitions dans la semaine"):
        weekly_plan.validate(plan(two))


# ------------------------------------------------------------------ solver

def test_the_three_race_firsts_fit_once_windows_are_gone():
    """The real case: W40 carries the extension, W43 is in assimilation."""
    verdict = transitions.diagnose(_problem())
    assert verdict.feasible is True
    schedule = verdict.options[0].schedule
    assert schedule.novelties["2026-W40"] == (LONG_RUN_STEP,)
    assert schedule.novelties["2026-W43"] == ()
    # A long-run step stacks with a transition: W41, W42, W44, W45 carry one.
    assert schedule.long_run_steps == 5
    for week in ("2026-W41", "2026-W42", "2026-W44", "2026-W45"):
        assert len([i for i in schedule.novelties[week] if i != LONG_RUN_STEP]) <= 1
    placed = {i for items in schedule.novelties.values() for i in items}
    assert {"nocturne", "descente", "reconnaissance"} <= placed


def test_too_many_firsts_for_the_weeks_left_names_the_levers():
    problem = _problem(weeks=("2026-W44", "2026-W45"), fixed={})
    verdict = transitions.diagnose(problem)
    assert verdict.feasible is False
    by_label = {option.label: option for option in verdict.options}
    assert by_label["sans 1re nocturne avant la course"].schedule is not None
    assert by_label["une transition de plus par semaine (garde-fou n°8)"].schedule is not None
    text = "\n".join(transitions.render(verdict))
    assert "INFAISABLE" in text and "ne tranche rien" in text


def test_assimilation_carries_nothing_new():
    schedule = transitions.solve(_problem(required=()))
    assert schedule.novelties["2026-W43"] == ()
    opened = transitions.solve(_problem(required=()), Rules(open_assimilation=True))
    assert opened.novelties["2026-W43"] == (LONG_RUN_STEP,)


def test_no_week_left_says_so():
    problem = _problem(weeks=(), fixed={})
    assert "Aucune semaine" in transitions.render(transitions.diagnose(problem))[-1]


def test_rendered_text_avoids_banned_glyphs():
    from redline.model import typography

    text = "\n".join(transitions.render(transitions.diagnose(_problem())))
    assert [glyph for glyph in typography.BANNED_GLYPHS if glyph in text] == []


# ------------------------------------------------------------------ reading the facts

class FakeStore(SimpleNamespace):
    def deadline_rows(self):
        return [{"kind": "race", "status": "scheduled", "due_on": RACE}]

    def active_plans(self):
        return self.plans


def test_build_problem_reads_plans_race_and_done_firsts():
    long_run = _session("w40-dim-sl", "2026-10-04", workout="long", distance_cap_km=16.0)
    rows = [SimpleNamespace(type="long", km=13.69, date=dt.date(2026, 9, 27))]
    store = FakeStore(plans={
        "2026-W37": {"sessions": [_session("w37-reco", "2026-09-10", "reconnaissance",
                                           status="done")]},
        "2026-W40": {"sessions": [long_run]},
    })
    problem = feasibility.build_problem(store, rows, dt.date(2026, 10, 1))
    assert problem.weeks == tuple(f"2026-W{n}" for n in range(40, 46))
    assert problem.taper == TAPER
    assert problem.fixed == {"2026-W40": (LONG_RUN_STEP,)}
    assert problem.required == ()
    assert problem.done == ("reconnaissance",)


def test_a_freed_week_is_planned_again_and_no_race_is_said():
    store = FakeStore(plans={"2026-W40": {"sessions": [
        _session("w40-nuit", "2026-10-02", "nocturne")]}})
    problem = feasibility.build_problem(store, [], dt.date(2026, 10, 1),
                                        frozenset({"2026-W40"}))
    assert problem.fixed == {}
    assert "reconnaissance" in problem.required
    store.deadline_rows = list
    assert "aucune course typée" in feasibility.report(store, [], dt.date(2026, 10, 1))[0]
