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
