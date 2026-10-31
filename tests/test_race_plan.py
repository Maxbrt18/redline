"""Race plan: the race grade curve, the instance's declared curve, passages, fuelling."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from conftest import ENGINE_ROOT, TEST_VAULT, cli_command
from test_route_profile import _gpx

from redline.analysis import race_pace
from redline.cli import race_plan
from redline.model import instance
from redline.model.errors import InputError

FIXTURE_TOML = (TEST_VAULT / "redline.toml").read_text(encoding="utf-8")


def test_the_strava_curve_is_one_on_the_flat_fastest_near_minus_nine():
    assert race_pace.strava_2017(0) == pytest.approx(1.0)
    downhill = {g: race_pace.strava_2017(g) for g in range(-20, 0)}
    assert max(downhill, key=downhill.get) in range(-11, -7)
    assert race_pace.strava_2017(-9) == pytest.approx(1.12, abs=0.02)
    assert race_pace.strava_2017(10) < race_pace.strava_2017(5) < 1.0


def test_declared_descents_replace_strava_only_below_zero():
    curve = race_pace.GradeCurve(descent=((-20, 0.9), (0, 1.0)))
    assert curve.speed(-10) == pytest.approx(0.95)
    assert curve.speed(8) == pytest.approx(race_pace.strava_2017(8))
    assert curve.fast_speed(8) == curve.speed(8)
    fast = race_pace.GradeCurve(training_climb=((0, 1.0), (10, 0.85)))
    assert fast.fast_speed(10) == pytest.approx(0.85)
    assert fast.fast_speed(10) > fast.speed(10)


def test_the_pace_curve_is_declared_in_the_instance():
    declared = instance.parse(FIXTURE_TOML + "\n[pace]\ndescent = [[-20, 0.9], [0, 1.0]]\n"
                                              "training_climb = [[0, 1.0], [12, 0.8]]\n")
    assert declared.descent_speed == ((-20.0, 0.9), (0.0, 1.0))
    assert declared.training_climb_speed == ((0.0, 1.0), (12.0, 0.8))
    assert instance.parse(FIXTURE_TOML).descent_speed == ()


@pytest.mark.parametrize("pace, message", [
    ("descent = [[-20, 0.9], [-30, 1.0]]", "strictly increasing"),
    ("descent = [[5, 0.9]]", "outside"),
    ("descent = [[-10, 3.0]]", "outside"),
    ("descent = [[-10]]", "each point"),
    ('training_climb = [["dix", 0.8]]', "each point"),
])
def test_a_malformed_pace_curve_is_refused(pace, message):
    with pytest.raises(instance.InstanceError, match=message):
        instance.parse(FIXTURE_TOML + f"\n[pace]\n{pace}\n")


def _flat(km=5.0):
    distance = np.arange(0.0, km * 1000 + 1, 5.0)
    return distance, np.full_like(distance, 100.0)


def test_a_flat_route_takes_its_distance_at_the_pace_plus_the_stops():
    distance, elevation = _flat()
    aids = [race_pace.parse_aid("2:Pont"), race_pace.parse_aid("4,5:Col")]
    result = race_pace.plan(distance, elevation, race_pace.GradeCurve(), 360.0, aids, 2.0)
    assert [p.name for p in result.passages] == ["Pont", "Col", ""]
    assert result.passages[0].hours == pytest.approx(12 / 60)
    assert result.passages[1].hours == pytest.approx((27 + 2) / 60)
    assert result.finish.hours == pytest.approx((30 + 4) / 60)
    assert result.passages[1].running_hours == pytest.approx(15 / 60)
    assert result.flat_equivalent_km == pytest.approx(5.0)


def test_a_climb_costs_time_and_counts_as_more_flat_kilometres():
    distance = np.arange(0.0, 2001, 5.0)
    elevation = 100 + 0.1 * distance
    result = race_pace.plan(distance, elevation, race_pace.GradeCurve(), 360.0)
    assert result.flat_equivalent_km > 2.0
    assert result.finish.hours > 12 / 60


@pytest.mark.parametrize("call, message", [
    (lambda: race_pace.parse_pace("5h30"), "M:SS"),
    (lambda: race_pace.parse_pace("1:00"), "between"),
    (lambda: race_pace.parse_pace("5:75"), "between"),
    (lambda: race_pace.parse_aid("douze:Pont"), "KM:NAME"),
    (lambda: race_pace.plan(*_flat(), race_pace.GradeCurve(), 360.0,
                            [race_pace.AidStation(9.0, "Ailleurs")]), "off the route"),
])
def test_bad_plan_inputs_are_refused(call, message):
    with pytest.raises(InputError, match=message):
        call()


def _report(tmp_path, **kwargs):
    from redline.analysis import race_report, route_profile
    track = route_profile.parse_gpx(_gpx(tmp_path).read_bytes())
    return race_report.build(track, pace_s_per_km=360.0, **kwargs)


def test_the_report_gives_clock_forecast_and_fuel_without_the_network(tmp_path):
    asked = []

    def forecaster(lat, lon, when):
        asked.append(when)
        return {"temp": 3.6, "feels_like": 0.4, "wind": 12.0, "precip": 0.2}

    report = _report(tmp_path, aids=[race_pace.parse_aid("2:Pont")], stop_minutes=1.0,
                     carbs_g_h=60, carbs_mix_above_g_h=50,
                     start_local=dt.datetime(2030, 11, 30, 23, 0),
                     zone=ZoneInfo("Europe/Paris"), forecaster=forecaster)
    first = report.passages[0]
    assert first.clock == dt.datetime(2030, 11, 30, 23, 0, tzinfo=ZoneInfo("Europe/Paris")) \
        + dt.timedelta(hours=first.hours)
    assert first.forecast.temp_c == pytest.approx(3.6)
    assert asked[0] == first.clock and len(asked) == 2
    assert first.carbs_g == pytest.approx(first.running_hours * 60)
    assert report.carbs_mixed and report.forecast_available is True
    assert first.low_hours < first.hours < first.high_hours
    payload = report.as_dict()
    assert payload["source"] == "gpx" and payload["passages"][0]["clock"].startswith("2030-11-30T23:12")
    assert race_plan._weather(first.forecast) == "4 °C (ressenti 0 °C), vent 12 km/h, pluie 0.2 mm/h"


def test_a_date_without_forecast_still_plans(tmp_path):
    def later(lat, lon, when):
        raise RuntimeError("beyond the forecast horizon")

    report = _report(tmp_path, start_local=dt.datetime(2030, 11, 30, 23, 0),
                     zone=ZoneInfo("Europe/Paris"), forecaster=later)
    assert report.forecast_available is False
    assert all(p.forecast is None and p.clock is not None for p in report.passages)


@pytest.mark.parametrize("kwargs, message", [
    ({"carbs_g_h": 95, "carbs_cap_g_h": 90}, "rule f1"),
    ({"stop_minutes": 45}, "0 to 30"),
    ({"start_local": dt.datetime(2030, 11, 30, 23, tzinfo=dt.UTC)}, "without an offset"),
    ({"start_local": dt.datetime(2030, 11, 30, 23)}, "time zone"),
])
def test_bad_report_inputs_are_refused(tmp_path, kwargs, message):
    with pytest.raises(InputError, match=message):
        _report(tmp_path, **kwargs)


def _run(tmp_path, *extra, vault=None):
    env = {k: v for k, v in os.environ.items() if k != "VAULT_PATH"}
    if vault:
        env["VAULT_PATH"] = str(vault)
    return subprocess.run([*cli_command("race-plan"), str(_gpx(tmp_path)), "--elevation", "gpx",
                           *extra], cwd=ENGINE_ROOT, env=env, capture_output=True, text=True,
                          check=False)


def test_the_command_plans_offline_with_the_default_curve(tmp_path):
    done = _run(tmp_path, "--pace", "6:00", "--aid", "2:Pont", "--stop", "1", "--carbs", "60",
                "--json")
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["curve_declared"] is False and payload["source"] == "gpx"
    assert payload["passages"][0]["hours"] == pytest.approx(0.2, abs=0.01)
    assert payload["passages"][0]["carbs_g"] == pytest.approx(12, abs=0.5)
    assert payload["model_error"] == [0.04, 0.085]


def test_the_command_reads_the_declared_curve_and_caps_fuelling(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "redline.toml").write_text(
        FIXTURE_TOML + "\n[pace]\ndescent = [[-20, 0.5], [0, 1.0]]\n", encoding="utf-8")
    done = _run(tmp_path, "--pace", "6:00", vault=vault)
    assert done.returncode == 0, done.stderr
    assert "descentes mesurées" in done.stdout
    refused = _run(tmp_path, "--pace", "6:00", "--carbs", "120", vault=vault)
    assert refused.returncode == 1 and "rule f1" in refused.stderr
