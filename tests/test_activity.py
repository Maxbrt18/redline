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


# ------------------------------------------------------------------ migration 0005

#: What the engine stored before the migration, produced by its `distill_activity`.
OLD_RUN = {
    "id": 90000000002, "nom": "Morning Run", "description": "jambes lourdes",
    "sport": "Run", "depart_local": "2026-08-14T07:32:50", "distance_km": 6.969,
    "temps_mouvement": "43:05", "temps_total": "43:20", "dplus_m": 64.6, "fc_moy": 148.2,
    "fc_max": 165.0, "effort_relatif": None, "calories": None, "materiel_id": "g1",
    "nb_laps": 2, "nb_athletes": 1, "allure_min_km": "6:11", "cadence_pas_min": 165.0,
    "meteo": {"polyline": "g|avGsvu[ICGEO", "time": "2026-08-14T07:32:50"},
    "laps": [
        {"rep": 1, "nom": "Lap 1", "distance_km": 1.0, "temps": "5:50", "fc_moy": 150.0,
         "fc_max": None, "dplus_m": 5.0, "allure_min_km": "5:50", "cadence_pas_min": 170.0},
        {"rep": 2, "nom": "Lap 2", "distance_km": 0.4, "temps": "1:30", "fc_moy": 172.0,
         "fc_max": None, "dplus_m": None, "allure_min_km": "3:45"},
    ],
}
OLD_RIDE = {
    "id": 3, "nom": None, "description": "", "sport": "Ride",
    "depart_local": "2026-08-15T10:00:00", "distance_km": 36.95,
    "temps_mouvement": "1:42:57", "temps_total": None, "dplus_m": None, "fc_moy": None,
    "fc_max": None, "effort_relatif": None, "calories": None, "materiel_id": None,
    "nb_laps": 0, "nb_athletes": None, "vitesse_kmh": 21.5, "cadence_tr_min": 80.0,
    "laps": [],
}


def _migration():
    import importlib.util

    from conftest import ENGINE_ROOT

    path = ENGINE_ROOT / "migrations" / "versions" / "20261028_0005_activity_in_english.py"
    spec = importlib.util.spec_from_file_location("activity_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_stored_activity_upgrades_to_the_contract_and_reads_the_same():
    migration = _migration()
    for old in (OLD_RUN, OLD_RIDE):
        activity = Activity.from_json(migration.to_english(old))
        shown = summary(activity)
        assert shown["moving_time"] == old["temps_mouvement"]
        assert shown.get("pace_min_per_km") == old.get("allure_min_km")
        assert shown.get("speed_kmh") == old.get("vitesse_kmh")
        assert activity.distance_km == old["distance_km"]
    run = Activity.from_json(migration.to_english(OLD_RUN))
    assert run.weather_query == OLD_RUN["meteo"]
    assert [lap.moving_s for lap in run.laps] == [350, 90]
    assert run.laps[0].cadence_spm == 170.0


def test_the_downgrade_gives_back_what_was_stored():
    migration = _migration()
    for old in (OLD_RUN, OLD_RIDE):
        assert migration.to_french(migration.to_english(old)) == old


def test_an_unknown_stored_key_stops_the_migration():
    with pytest.raises(RuntimeError, match="inconnues"):
        _migration().to_english({**OLD_RIDE, "puissance": 210})
