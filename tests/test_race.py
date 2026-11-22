"""A typed race and the taper it carries."""

import datetime as dt

import pytest

from redline.model import race
from redline.model.schema import SchemaError


def test_a_race_falls_back_on_the_rulebook_for_what_it_leaves_unsaid():
    typed = race.from_row({"kind": "race", "status": "scheduled", "due_on": "2030-11-30"})
    assert typed.on == dt.date(2030, 11, 30)
    assert (typed.taper_weeks, typed.taper_cut_min_pct, typed.taper_cut_max_pct) == (3, 41, 60)


def test_a_race_overrides_the_rulebook():
    typed = race.from_row({"kind": "race", "status": "scheduled",
                           "due_on": dt.date(2030, 4, 13),
                           "context": {"taper_weeks": 1, "taper_cut_max_pct": 50,
                                       "distance_km": 10}})
    assert (typed.taper_weeks, typed.taper_cut_min_pct, typed.taper_cut_max_pct) == (1, 41, 50)


def test_the_taper_keys_are_checked_and_the_others_left_alone():
    race.CONTEXT_SCHEMA.validate({"taper_weeks": 2, "start_time": "23:00"})
    with pytest.raises(SchemaError, match="taper_weeks"):
        race.CONTEXT_SCHEMA.validate({"taper_weeks": 9})
    with pytest.raises(SchemaError, match="is above taper_cut_max_pct"):
        race.CONTEXT_SCHEMA.validate({"taper_cut_min_pct": 70, "taper_cut_max_pct": 50})


def test_the_upcoming_race_skips_the_ones_run_or_done():
    class Store:
        def deadline_rows(self):
            return [{"kind": "race", "status": "done", "due_on": "2030-09-01"},
                    {"kind": "general", "status": "todo", "due_on": "2030-10-01"},
                    {"kind": "race", "status": "scheduled", "due_on": "2030-11-30"},
                    {"kind": "race", "status": "scheduled", "due_on": "2030-06-01"}]
    assert race.upcoming(Store(), dt.date(2030, 7, 1)).on == dt.date(2030, 11, 30)
    assert race.upcoming(Store(), dt.date(2030, 12, 1)) is None
    assert [typed.on.month for typed in race.typed(Store())] == [6, 9, 11]


def test_a_race_carries_what_its_plan_needs():
    typed = race.from_row({"kind": "race", "status": "scheduled", "due_on": "2030-11-30",
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
    typed = race.from_row({"kind": "race", "status": "scheduled", "due_on": "2030-11-30",
                           "context": {"taper_weeks": 1, "pace_s_per_km": "fast"}})
    assert typed.taper_weeks == 1 and typed.pace_s_per_km is None


GOAL = {"distance_km": 21.1, "terrain": "road", "peak_volume_km": 50, "peak_long_run_km": 20,
        "quality_per_week": 2, "families": ["threshold"], "goal_time_s": 6300,
        "goal_source": "coach",
        "skeleton": {"candidate": "balanced", "specific_weeks": 6, "deload_every": 4,
                     "second_quality": "specific"}}


def test_a_race_carries_the_goal_its_plan_is_built_from():
    typed = race.from_row({"kind": "race", "status": "scheduled", "due_on": "2031-04-06",
                           "context": GOAL})
    goal = typed.goal
    assert (goal.distance_km, goal.terrain, goal.peak_volume_km) == (21.1, "road", 50)
    assert goal.families == ("threshold",) and goal.goal_time_s == 6300
    assert goal.skeleton == race.Skeleton("balanced", 6, 4, "specific")
    assert race.from_row({"kind": "race", "status": "scheduled",
                          "due_on": "2031-04-06"}).goal is None


def test_a_race_that_says_no_taper_takes_its_templates_by_distance():
    def taper(context):
        return race.from_row({"kind": "race", "status": "scheduled", "due_on": "2031-04-06",
                              "context": context}).taper_weeks

    assert taper({"distance_km": 21.1, "terrain": "road"}) == 2
    assert taper({"distance_km": 42.2, "terrain": "road"}) == 3
    assert taper({"distance_km": 21.1, "terrain": "road", "taper_weeks": 1}) == 1, \
        "the race's own taper first"
    assert taper({"distance_km": 21.1}) == 3, "without a terrain, the rulebook's"


def test_the_goal_keys_are_typed():
    race.CONTEXT_SCHEMA.validate(GOAL)
    with pytest.raises(SchemaError, match="goal_time_s and goal_source go together"):
        race.CONTEXT_SCHEMA.validate({"goal_time_s": 6300})
    with pytest.raises(SchemaError, match="terrain"):
        race.CONTEXT_SCHEMA.validate({"terrain": "track"})
    with pytest.raises(SchemaError, match="second_quality"):
        race.CONTEXT_SCHEMA.validate({"skeleton": {**GOAL["skeleton"], "second_quality": "base"}})
