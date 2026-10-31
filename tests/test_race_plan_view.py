"""The race plan the notebook serves: the route from the vault, the terrain kept on disk,
the forecast at each passage."""

import datetime as dt
import json
import time

import contract
import pytest

pytest.importorskip("scipy")

from test_route_profile import _gpx, _terrain

from redline.model import notebook_views as views
from redline.model import race as race_model
from redline.service import notebook_weather, race_plan


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REDLINE_STATE_DIR", str(tmp_path / "state"))
    race_plan._profiles.clear()
    race_plan._measuring.clear()
    calls = []

    def terrain(lats, lons):
        calls.append(len(lats))
        return _terrain(lats, lons)
    from redline.integrations import ign_altimetry
    monkeypatch.setattr(ign_altimetry, "elevations", terrain)
    return calls


def _vault(tmp_path):
    root = tmp_path / "vault"
    (root / "coaching").mkdir(parents=True)
    _gpx(tmp_path).rename(root / "coaching" / "route.gpx")
    return root


def _race(**context):
    return race_model.from_row({
        "kind": "race", "status": "scheduled", "due_on": "2030-11-30",
        "context": {"route_gpx": "coaching/route.gpx", "start_time": "23:00",
                    "aid_stations": [{"km": 2, "name": "A"}, {"km": 3.5, "name": "B"}],
                    "stop_minutes": 2, "carbs_g_h": 60, **context}})


def _measured(root, race):
    """The first request starts the measurement and answers Pending; the next ones read it."""
    with pytest.raises(race_plan.Pending):
        race_plan.view(race, root, None, dt.date(2030, 11, 20))
    for _ in range(200):
        if not race_plan._measuring:
            break
        time.sleep(0.01)


def test_the_terrain_is_measured_once_in_the_background_then_read_from_disk(tmp_path, isolated):
    root = _vault(tmp_path)
    assert race_plan.plan(_race(), root, None) is None
    _measured(root, _race())
    plan = race_plan.plan(_race(pace_s_per_km=330), root, None)
    assert plan is not None and plan["source"] == "ign"
    race_plan._profiles.clear()
    assert race_plan.plan(_race(), root, None)["source"] == "ign"
    assert len(isolated) == 1


def test_the_plan_is_what_the_page_reads(tmp_path):
    root = _vault(tmp_path)
    _measured(root, _race())
    plan = race_plan.plan(_race(pace_s_per_km=330), root, None)
    assert contract.problems(views.RacePlan, plan) == []
    assert [item["name"] for item in plan["passages"]] == ["A", "B", ""]
    assert [item["to_km"] for item in plan["stretches"]] == [2.0, 3.5, plan["length_km"]]
    assert plan["profile"][0][0] == 0 and plan["profile"][-1][0] == plan["length_km"]
    assert plan["start"] == "2030-11-30T23:00" and plan["reference_pace_s"] == 330


def test_the_page_retimes_at_any_pace_from_the_reference(tmp_path):
    """Running time scales with the pace, the stops do not: what `raceplan.js` relies on."""
    root = _vault(tmp_path)
    _measured(root, _race())
    slow = race_plan.plan(_race(pace_s_per_km=400), root, None)
    fast = race_plan.plan(_race(pace_s_per_km=300), root, None)
    stop = 2 / 60
    for index, (a, b) in enumerate(zip(slow["passages"], fast["passages"], strict=True)):
        assert (a["hours"] - index * stop) * 300 / 400 == pytest.approx(b["hours"] - index * stop)
        assert a["running_hours"] * 300 / 400 == pytest.approx(b["running_hours"])


def test_without_a_chosen_pace_the_plan_says_so(tmp_path):
    root = _vault(tmp_path)
    _measured(root, _race())
    plan = race_plan.plan(_race(), root, None)
    assert plan["pace_s_per_km"] is None and plan["reference_pace_s"] == race_plan.REFERENCE_PACE_S


def test_a_route_outside_the_vault_is_refused(tmp_path):
    root = _vault(tmp_path)
    (tmp_path / "outside.gpx").write_text("<gpx/>")
    assert race_plan.plan(_race(route_gpx="../outside.gpx"), root, None) is None
    assert race_plan.plan(_race(route_gpx="coaching/missing.gpx"), root, None) is None
    assert race_plan.plan(None, root, None) is None


def test_a_terrain_service_down_falls_back_on_the_gpx_and_asks_again_later(tmp_path, monkeypatch):
    from redline.integrations import ign_altimetry

    def down(lats, lons):
        raise OSError("unreachable")
    monkeypatch.setattr(ign_altimetry, "elevations", down)
    root = _vault(tmp_path)
    _measured(root, _race())
    assert race_plan.plan(_race(), root, None)["source"] == "gpx"
    assert not list((tmp_path / "state").glob("elevation/*.json"))


def test_the_forecast_comes_hour_by_hour_at_each_passage(tmp_path, monkeypatch):
    def forecast(lat, lon, day):
        times = [f"{day.isoformat()}T{hour:02d}:00" for hour in range(24)]
        return {"hourly": {"time": times, "temperature_2m": [4.4] * 24,
                           "apparent_temperature": [1.6] * 24,
                           "precipitation_probability": [10] * 24,
                           "wind_speed_10m": [12.2] * 24, "weather_code": [0] * 24,
                           "is_day": [0] * 24}}
    monkeypatch.setattr(notebook_weather, "_forecast", forecast)
    root = _vault(tmp_path)
    _measured(root, _race())
    view = race_plan.view(_race(pace_s_per_km=330), root, None, dt.date(2030, 11, 20))
    assert contract.problems(views.RacePlanView, view) == []
    assert len(view["weather"]) == 3
    first = view["weather"][0]
    assert first[0] == {"time": "2030-11-30T23:00", "temp": 4, "feels": 2, "rain": 10,
                        "wind": 12, "code": 0, "day": False}
    assert first[-1]["time"] == "2030-12-01T13:00"


def test_no_forecast_before_it_reaches_the_race_or_without_a_start(tmp_path, monkeypatch):
    monkeypatch.setattr(notebook_weather, "_forecast", lambda *_: pytest.fail("asked"))
    root = _vault(tmp_path)
    _measured(root, _race())
    assert race_plan.view(_race(), root, None, dt.date(2030, 10, 1))["weather"] is None
    assert race_plan.view(_race(start_time=None), root, None, dt.date(2030, 11, 20))["weather"] is None


def test_the_elevations_are_kept_as_plain_json(tmp_path):
    root = _vault(tmp_path)
    _measured(root, _race())
    kept = list((tmp_path / "state").glob("elevation/*.json"))
    assert len(kept) == 1 and isinstance(json.loads(kept[0].read_text())[0], float)
