"""A typed race: what its plan needs and the goal its preparation is built from."""

import datetime as dt

import pytest

from redline.model import race
from redline.model.schema import SchemaError
from redline.model.vocabulary import RaceState


def test_a_race_carries_no_taper_its_preparation_holds_it():
    typed = race.from_row({"kind": "race", "status": "scheduled", "priority": "a",
                           "race_state": "active", "due_on": "2030-11-30",
                           "context": {"distance_km": 10, "terrain": "road",
                                       "taper_weeks": 1, "taper_cut_max_pct": 50}})
    assert typed.on == dt.date(2030, 11, 30)
    assert not hasattr(typed, "taper_weeks")
    race.CONTEXT_SCHEMA.validate({"taper_weeks": 9})


def _row(on, priority, state, kind="race"):
    return {"kind": kind, "priority": priority, "race_state": state,
            "status": race.status_of(RaceState(state)) if state else "todo", "due_on": on}


class Calendar:
    def deadline_rows(self):
        return [_row("2030-09-01", "a", "run"), _row("2030-10-01", None, None, "general"),
                _row("2030-10-12", "b", "active"), _row("2030-10-19", "c", "cancelled"),
                _row("2030-11-30", "a", "active"), _row("2031-02-01", "c", "active")]


def test_the_goal_is_the_active_race_of_priority_a():
    assert race.active_goal(Calendar()).on == dt.date(2030, 11, 30)
    assert [typed.on.month for typed in race.typed(Calendar())] == [9, 10, 10, 11, 2]


def test_the_next_race_is_the_next_active_one_whatever_its_priority():
    assert race.next_race(Calendar(), dt.date(2030, 9, 2)).on == dt.date(2030, 10, 12)
    assert race.next_race(Calendar(), dt.date(2030, 10, 12)).on == dt.date(2030, 10, 12), \
        "a race is next on its own day"
    assert race.next_race(Calendar(), dt.date(2031, 2, 2)) is None


def test_a_goal_whose_day_passed_stays_the_goal_until_its_outcome_is_written():
    assert race.active_goal(Calendar()).on == dt.date(2030, 11, 30)
    assert race.next_race(Calendar(), dt.date(2030, 12, 1)).on == dt.date(2031, 2, 1)


def test_a_race_carries_what_its_plan_needs():
    typed = race.from_row({"kind": "race", "status": "scheduled", "priority": "a", "race_state": "active", "due_on": "2030-11-30",
                           "athlete_label": "Course fictive",
                           "context": {"start_time": "23:00", "route_gpx": "coaching/route.gpx",
                                       "aid_stations": [{"km": 26, "name": "B"},
                                                        {"km": 12, "name": "A"}],
                                       "stop_minutes": 2.5, "carbs_g_h": 60,
                                       "pace_s_per_km": 330}})
    assert typed.name == "Course fictive" and typed.start_time == dt.time(23, 0)
    assert [aid.name for aid in typed.aid_stations] == ["A", "B"]
    assert (typed.route_gpx, typed.stop_minutes, typed.carbs_g_h, typed.pace_s_per_km) == (
        "coaching/route.gpx", 2.5, 60, 330.0)


def test_the_plan_keys_are_checked_on_write():
    with pytest.raises(SchemaError, match="route_gpx"):
        race.CONTEXT_SCHEMA.validate({"route_gpx": "route.kml"})
    with pytest.raises(SchemaError, match="name"):
        race.CONTEXT_SCHEMA.validate({"aid_stations": [{"km": 12}]})
    with pytest.raises(SchemaError, match="pace_s_per_km"):
        race.CONTEXT_SCHEMA.validate({"pace_s_per_km": 60})


def test_an_unreadable_key_costs_its_value_never_the_race():
    typed = race.from_row({"kind": "race", "status": "scheduled", "priority": "a", "race_state": "active", "due_on": "2030-11-30",
                           "context": {"carbs_g_h": 60, "pace_s_per_km": "fast"}})
    assert typed.carbs_g_h == 60 and typed.pace_s_per_km is None


GOAL = {"distance_km": 21.1, "terrain": "road", "goal_time_s": 6300, "goal_source": "coach"}


def test_a_race_carries_the_goal_its_plan_is_built_from():
    typed = race.from_row({"kind": "race", "status": "scheduled", "priority": "a", "race_state": "active", "due_on": "2031-04-06",
                           "context": GOAL})
    goal = typed.goal
    assert (goal.distance_km, goal.terrain, goal.goal_time_s) == (21.1, "road", 6300)
    assert typed.distance_km == 21.1
    assert race.from_row({"kind": "race", "status": "scheduled", "priority": "a", "race_state": "active",
                          "due_on": "2031-04-06"}).goal is None


def test_the_goal_keys_are_typed():
    race.CONTEXT_SCHEMA.validate(GOAL)
    with pytest.raises(SchemaError, match="goal_time_s and goal_source go together"):
        race.CONTEXT_SCHEMA.validate({"goal_time_s": 6300})
    with pytest.raises(SchemaError, match="terrain"):
        race.CONTEXT_SCHEMA.validate({"terrain": "track"})


def test_the_skeleton_s_keys_are_the_cycle_version_s_and_left_unread():
    retired = {**GOAL, "peak_volume_km": 50, "skeleton": {"candidate": "balanced"}}
    race.CONTEXT_SCHEMA.validate(retired)
    typed = race.from_row({"kind": "race", "status": "scheduled", "priority": "a",
                           "race_state": "active", "due_on": "2031-04-06",
                           "deadline_key": "half", "context": retired})
    assert typed.unread == () and typed.key == "half"
    assert not hasattr(typed.goal, "peak_volume_km")


@pytest.mark.parametrize(("before", "after", "allowed"), [
    (None, "active", True), (None, "proposed", True), (None, "run", False),
    ("proposed", "active", True), ("proposed", "dropped", True), ("proposed", "run", False),
    ("active", "run", True), ("active", "abandoned", True), ("active", "cancelled", True),
    ("active", "postponed", True), ("postponed", "active", True),
    ("postponed", "cancelled", True), ("postponed", "run", False),
    ("run", "active", False), ("cancelled", "active", False), ("active", "active", True),
])
def test_a_race_moves_only_along_its_life(before, after, allowed):
    found = race.move_error(None if before is None else RaceState(before), RaceState(after))
    assert (found is None) is allowed, found


def test_a_race_is_done_once_its_state_is_closed():
    assert [race.status_of(state) for state in RaceState] == [
        "scheduled", "scheduled", "scheduled", "done", "done", "done", "done"]


def test_a_race_declares_the_first_times_it_requires_among_the_rulebook_s_kinds():
    context = {**GOAL, "firsts": ["night", "reconnaissance"]}
    race.CONTEXT_SCHEMA.validate(context)
    typed = race.from_row({"kind": "race", "status": "scheduled", "priority": "a",
                           "race_state": "active", "due_on": "2031-04-06", "context": context})
    assert typed.goal is not None and typed.goal.firsts == ("night", "reconnaissance")
    with pytest.raises(SchemaError, match="no transition kind of the rulebook"):
        race.CONTEXT_SCHEMA.validate({**GOAL, "firsts": ["swim"]})
