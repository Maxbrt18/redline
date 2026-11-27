"""The plan follows the goal, the active race of priority A, and nothing else."""

from __future__ import annotations

import datetime as dt

import trajectory

from redline.analysis import plan_engine, taper
from redline.model import iso_week, race
from redline.model.vocabulary import RaceState
from redline.service import plan_revision

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


TEN = other("ten", dt.date(2031, 2, 9), "b", "active", distance_km=10, terrain="road")


def test_a_race_b_before_the_goal_does_not_take_the_preparation_over():
    skeleton = plan_engine.chosen(store(TEN), TODAY)
    assert skeleton.weeks[-1].label == iso_week.of(trajectory.RACE_ON)
    assert taper.race_window(store(TEN), iso_week.of(TEN["due_on"])) is None
    assert taper.race_window(store(TEN), iso_week.of(trajectory.RACE_ON)) is not None


def test_only_a_race_run_or_started_opens_a_recovery():
    def recovering(state):
        ran = other("cross", TODAY - dt.timedelta(days=6), "a", state)
        return plan_engine.frame(store(ran), TODAY).recovering

    assert recovering("run") and recovering("abandoned")
    assert not recovering("cancelled")


def test_a_goal_whose_day_passed_refuses_the_next_week_until_its_outcome_is_written():
    after = trajectory.RACE_ON + dt.timedelta(days=2)
    week = iso_week.of(after + dt.timedelta(days=7))
    revision = trajectory.revision(week, 20, 8, after)
    [refusal] = plan_revision.skeleton_errors(store(), week, revision, None, print)
    assert "is past: write its outcome" in refusal
