"""The activity contract: one shape stored, read back the same, shown readable."""

import dataclasses

import pytest

from redline.model.activity import Activity, ActivityError, Lap, clock, summary


def _activity(**fields):
    return Activity(**{"id": 7, "sport": "Run", "distance_m": 10000.0, "moving_s": 3000,
                       "laps": (Lap(distance_m=1000.0, moving_s=300, hr_avg_bpm=150.0),),
                       **fields})


def test_an_activity_reads_back_exactly_as_it_was_stored():
    activity = _activity(start_local="2026-11-03T18:30:00", route_polyline="abc")
    assert Activity.from_json(activity.to_json()) == activity


def test_a_stored_activity_with_an_unknown_key_is_refused():
    with pytest.raises(ActivityError, match="unreadable"):
        Activity.from_json({**_activity().to_json(), "nom": "Morning Run"})


def test_pace_is_derived_and_only_on_foot():
    assert _activity().pace_s_per_km == 300
    assert _activity(sport="Ride").pace_s_per_km is None


def test_a_pace_just_under_the_minute_does_not_read_sixty_seconds():
    assert clock(419.7) == "7:00"
    assert clock(3725) == "1:02:05"


def test_the_summary_is_readable_and_never_carries_the_route():
    out = summary(_activity(route_polyline="abc", elapsed_s=3100))
    assert out["moving_time"] == "50:00"
    assert out["elapsed_time"] == "51:40"
    assert out["pace_min_per_km"] == "5:00"
    assert out["lap_count"] == 1
    assert "route_polyline" not in out and "laps" not in out


def test_every_stored_field_is_either_shown_or_named_as_not_shown():
    shown = set(summary(_activity(
        name="n", description="d", start_local="s", elapsed_s=1, elevation_gain_m=1,
        hr_avg_bpm=1, hr_max_bpm=1, cadence_spm=1, relative_effort=1, calories=1,
        gear_id="g", athlete_count=1)))
    stored = {field.name for field in dataclasses.fields(Activity)}
    renamed = {"distance_m": "distance_km", "moving_s": "moving_time",
               "elapsed_s": "elapsed_time", "laps": "lap_count"}
    assert stored - set(renamed) - {"route_polyline", "cadence_rpm"} <= shown
    assert set(renamed.values()) <= shown
