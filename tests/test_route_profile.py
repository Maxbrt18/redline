"""The elevation profile of a race route: terrain model, GPX fallback, climb by stretch."""

from __future__ import annotations

import io
import json
import math
import subprocess
import urllib.error

import numpy as np
import pytest
from conftest import ENGINE_ROOT, cli_command

from redline.analysis import route_profile
from redline.integrations import ign_altimetry
from redline.model.errors import ExternalError, InputError
from redline.model.vocabulary import ElevationSource

#: Metres per degree of latitude.
DEGREE_M = 111_195.0


def _hill(x_m: float) -> float:
    """A fictional route: 2 km flat, 1 km at +10 %, 1 km at -10 %, 1 km flat."""
    if x_m < 2000:
        return 100.0
    if x_m < 3000:
        return 100.0 + (x_m - 2000) * 0.10
    if x_m < 4000:
        return 200.0 - (x_m - 3000) * 0.10
    return 100.0


def _gpx(tmp_path, step_m=25.0, length_m=5000.0, ele=True, name="Course fictive"):
    rows = []
    x = 0.0
    while x <= length_m:
        elevation = f"<ele>{round(_hill(x))}</ele>" if ele else ""
        rows.append(f'<trkpt lat="{45 + x / DEGREE_M:.7f}" lon="4.0">{elevation}</trkpt>')
        x += step_m
    path = tmp_path / "route.gpx"
    path.write_text('<?xml version="1.0"?><gpx version="1.1" '
                    'xmlns="http://www.topografix.com/GPX/1/1">'
                    f"<trk><name>{name}</name><trkseg>{''.join(rows)}</trkseg></trk></gpx>")
    return path


def _terrain(lats, lons):
    """A fictional terrain model: the same hill, with 0.3 m of micro-relief every 5 m."""
    return [_hill((lat - 45) * DEGREE_M) + 0.3 * math.sin(i) for i, lat in enumerate(lats)]


def test_the_terrain_model_profile_is_smoothed_and_carries_its_band(tmp_path):
    profile = route_profile.profile(route_profile.read_gpx(_gpx(tmp_path)), _terrain)

    assert profile.source is ElevationSource.IGN
    assert profile.length_km == pytest.approx(5.0, abs=0.01)
    assert profile.dplus_m == pytest.approx(100, abs=3)
    assert profile.dminus_m == pytest.approx(100, abs=3)
    low, high = profile.dplus_band_m
    assert low <= profile.dplus_m <= high
    assert profile.highest()[1] == pytest.approx(3.0, abs=0.05)


def test_smoothing_removes_the_micro_relief_a_raw_sum_would_count(tmp_path):
    lats = [45 + x / DEGREE_M for x in range(0, 5001, 5)]
    raw = route_profile._totals(np.array(_terrain(lats, [4.0] * len(lats))))
    assert raw[0] > 150
    smoothed = route_profile.profile(route_profile.read_gpx(_gpx(tmp_path)), _terrain)
    assert smoothed.dplus_m < 105


def test_the_climb_of_each_stretch_adds_up_to_the_route(tmp_path):
    profile = route_profile.profile(route_profile.read_gpx(_gpx(tmp_path)), _terrain)
    climb = profile.climb(2.0, 3.0)
    assert climb[0] == pytest.approx(100, abs=5)
    up = sum(profile.climb(a, b)[0] for a, b in ((0, 2.5), (2.5, profile.length_km)))
    assert up == pytest.approx(profile.dplus_m, abs=1)
    with pytest.raises(InputError, match="not a stretch"):
        profile.climb(4.0, 9.0)


def test_without_full_coverage_the_gpx_elevation_is_used_unsmoothed(tmp_path):
    track = route_profile.read_gpx(_gpx(tmp_path))

    def border(lats, lons):
        values = _terrain(lats, lons)
        values[-1] = None
        return values

    profile = route_profile.profile(track, border)
    assert profile.source is ElevationSource.GPX
    assert profile.dplus_band_m is None
    assert profile.dplus_m == pytest.approx(100, abs=3)
    assert route_profile.profile(track).source is ElevationSource.GPX


def test_a_route_without_any_elevation_is_refused(tmp_path):
    track = route_profile.read_gpx(_gpx(tmp_path, ele=False))
    assert track.ele is None
    with pytest.raises(InputError, match="no elevation"):
        route_profile.profile(track)


@pytest.mark.parametrize("content, message", [
    ("pas du xml", "unreadable"),
    ('<gpx><trk><trkseg><trkpt lat="45" lon="4"><ele>1</ele></trkpt></trkseg></trk></gpx>',
     "without a track"),
    ('<gpx><trk><trkseg><trkpt lat="x" lon="4"/><trkpt lat="45" lon="4"/></trkseg></trk></gpx>',
     "lat/lon"),
])
def test_an_unusable_gpx_is_refused_with_its_reason(tmp_path, content, message):
    path = tmp_path / "bad.gpx"
    path.write_text(content)
    with pytest.raises(InputError, match=message):
        route_profile.read_gpx(path)


def test_an_oversized_gpx_is_refused_before_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(route_profile, "MAX_FILE_BYTES", 100)
    with pytest.raises(InputError, match="too large"):
        route_profile.read_gpx(_gpx(tmp_path))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_altimetry_client_batches_and_reads_no_data_as_none(monkeypatch):
    monkeypatch.setattr(ign_altimetry, "BATCH", 2)
    sent = []

    def opener(request, timeout):
        body = json.loads(request.data)
        sent.append(body)
        values = [ign_altimetry.NO_DATA if lat.startswith("47") else 100.0
                  for lat in body["lat"].split("|")]
        return _Response(json.dumps({"elevations": values}).encode())

    out = ign_altimetry.elevations([45.0, 45.1, 47.5], [4.0, 4.1, 11.0],
                                   opener=opener, sleep=lambda _: None)
    assert out == [100.0, 100.0, None]
    assert len(sent) == 2 and sent[0]["resource"] == ign_altimetry.RESOURCE


def test_an_unreachable_altimetry_service_is_an_external_error():
    def opener(request, timeout):
        raise urllib.error.URLError("offline")

    with pytest.raises(ExternalError, match="unavailable"):
        ign_altimetry.elevations([45.0], [4.0], opener=opener, sleep=lambda _: None)


def test_the_command_cuts_the_route_at_the_aid_stations_offline(tmp_path):
    done = subprocess.run(
        [*cli_command("route-profile"), str(_gpx(tmp_path)), "--elevation", "gpx",
         "--at", "3", "--json"],
        cwd=ENGINE_ROOT, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["source"] == "gpx" and payload["dplus_band_m"] is None
    assert [(s["from_km"], s["to_km"]) for s in payload["stretches"]] == [(0.0, 3.0), (3.0, 5.0)]
    assert payload["stretches"][0]["dplus_m"] == pytest.approx(100, abs=3)
    assert payload["stretches"][1]["dminus_m"] == pytest.approx(100, abs=3)


def test_an_uploaded_gpx_with_a_document_type_is_refused(tmp_path):
    data = _gpx(tmp_path).read_bytes().replace(
        b"<gpx ", b'<!DOCTYPE gpx [<!ENTITY a "aaaa">]><gpx ', 1)
    with pytest.raises(InputError, match="document type"):
        route_profile.parse_gpx(data)
    assert route_profile.parse_gpx(_gpx(tmp_path).read_bytes()).name == "Course fictive"
