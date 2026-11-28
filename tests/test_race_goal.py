"""The plan follows the goal, the active race of priority A, and nothing else."""

from __future__ import annotations

import datetime as dt

import trajectory

from redline.analysis import taper
from redline.model import cycle_templates, cycle_transitions, iso_week, race
from redline.model.cycle_transitions import Standing
from redline.model.vocabulary import CycleKind, RaceState

FIRST = trajectory.FIRST_WEEK
TODAY = iso_week.monday(FIRST)


def other(key, on, priority, state, **context):
    return {"deadline_key": key, "kind": "race", "priority": priority, "race_state": state,
            "status": race.status_of(RaceState(state)), "due_on": on, "item": key,
            "context": context, "fact_owner": "athlete/goals.md", "athlete_label": key}


def store(*others):
    found = trajectory.Store(trajectory.HALF, trajectory.RACE_ON, others)
    for back in range(1, trajectory.HISTORY_WEEKS + 1):
        trajectory.run_week(found, iso_week.shift(FIRST, -back), 30, 14)
    return found


TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)
TEN = other("ten", dt.date(2031, 2, 9), "b", "active", distance_km=10, terrain="road")


def test_a_race_b_before_the_goal_does_not_take_the_taper_over():
    held = store(TEN)
    trajectory.take(held, trajectory.window_start())
    assert taper.race_window(held, iso_week.of(TEN["due_on"])) is None
    assert taper.race_window(held, iso_week.of(trajectory.RACE_ON)) is not None
    assert taper.race_window(store(TEN), iso_week.of(trajectory.RACE_ON)) is None, \
        "no preparation holds the race: no taper is planned"


def test_only_a_race_run_or_started_opens_a_recovery():
    def kind(state):
        ran = other("cross", TODAY - dt.timedelta(days=6), "a", state, distance_km=10)
        transition = cycle_transitions.next_cycle(
            None, race.typed(store(ran)), (), Standing(30, 30, 15), TODAY,
            TEMPLATES)
        assert transition is not None
        return transition.kind

    assert kind("run") == kind("abandoned") == CycleKind.RECOVERY
    assert kind("cancelled") == CycleKind.GENERAL
