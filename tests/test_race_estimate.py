"""A road goal's time from the declared all-out efforts: Daniels' curve, a local-level
filter, the extrapolation margin, the break that caps it and the sessions that bound it."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from redline.analysis import ledger, race_estimate
from redline.model.efforts import Break, Effort
from redline.model.vocabulary import EffortKind, EffortTerrain

TODAY = dt.date(2030, 11, 27)
HALF_KM = 21.0975
CONFIG = race_estimate.Settings(window_weeks=78, min_source_km=3.0, noise=1.2, drift=0.25,
                                exponent_sd=0.02, band_pct=80, break_min_days=21,
                                bound_weeks=8)


@pytest.mark.parametrize(("km", "seconds"), [
    (5, 19 * 60 + 57), (10, 41 * 60 + 21), (HALF_KM, 3600 + 31 * 60 + 35),
    (42.195, 3 * 3600 + 10 * 60 + 49)])
def test_the_published_table_reads_as_one_vdot(km, seconds):
    """Daniels' table, VDOT 50: 5 km 19:57, 10 km 41:21, half 1:31:35, marathon 3:10:49."""
    assert race_estimate.vdot(km, seconds) == pytest.approx(50, abs=0.1)


def test_the_time_at_another_distance_keeps_the_vdot():
    assert race_estimate.time_for(50, HALF_KM) == pytest.approx(3600 + 31 * 60 + 35, abs=10)


def effort(days_ago: int, km: float, minutes: float, terrain=EffortTerrain.FLAT,
           key: str = "") -> Effort:
    return Effort(key or f"e{days_ago}", TODAY - dt.timedelta(days=days_ago), km,
                  minutes * 60, terrain, EffortKind.RACE)


def test_a_single_effort_keeps_its_value_and_widens_with_its_age():
    recent = race_estimate.estimate([effort(30, 10, 40)], [], TODAY, HALF_KM, CONFIG)
    old = race_estimate.estimate([effort(400, 10, 40)], [], TODAY, HALF_KM, CONFIG)
    assert recent is not None and old is not None
    assert recent.vdot == pytest.approx(old.vdot) == pytest.approx(
        race_estimate.vdot(10, 40 * 60))
    assert old.vdot_sd > recent.vdot_sd
    assert old.high_s - old.low_s > recent.high_s - recent.low_s
    assert old.low_s < old.time_s < old.high_s
    assert [source.share for source in old.sources] == [1.0]


def test_a_recent_effort_weighs_more_than_an_old_faster_one():
    found = race_estimate.estimate([effort(410, 10, 39.5), effort(140, 10, 46.5)], [],
                                   TODAY, HALF_KM, CONFIG)
    assert found is not None
    old, recent = found.sources
    assert recent.share > old.share and old.share + recent.share == pytest.approx(1)
    assert recent.vdot < found.vdot < old.vdot
    assert found.vdot == pytest.approx(old.share * old.vdot + recent.share * recent.vdot)


def test_the_farther_the_source_the_wider_the_range():
    near = race_estimate.estimate([effort(10, 10, 40)], [], TODAY, HALF_KM, CONFIG)
    far = race_estimate.estimate([effort(10, 3.6, 13)], [], TODAY, HALF_KM, CONFIG)
    assert near is not None and far is not None
    assert math.log(far.high_s / far.time_s) > math.log(near.high_s / near.time_s)


def test_a_long_break_after_the_effort_leaves_only_the_slow_half():
    efforts = [effort(400, 10, 40)]
    pause = Break("shin", TODAY - dt.timedelta(days=300), TODAY - dt.timedelta(days=250))
    short = Break("cold", TODAY - dt.timedelta(days=100), TODAY - dt.timedelta(days=95))
    capped = race_estimate.estimate(efforts, [pause, short], TODAY, HALF_KM, CONFIG)
    free = race_estimate.estimate(efforts, [short], TODAY, HALF_KM, CONFIG)
    assert capped is not None and free is not None
    assert capped.capped_by == pause and capped.low_s == capped.time_s
    assert free.capped_by is None and free.low_s < free.time_s
    assert capped.high_s == pytest.approx(free.high_s)


def test_hilly_short_or_old_efforts_give_no_estimate():
    efforts = [effort(30, 15, 70, EffortTerrain.HILLY), effort(30, 2, 7),
               effort(7 * 80, 10, 40)]
    assert race_estimate.estimate(efforts, [], TODAY, HALF_KM, CONFIG) is None


def test_the_bound_is_the_best_piece_within_the_curve():
    pieces = [(TODAY, 0.74, 180.0), (TODAY, 7.06, 1800.0), (TODAY, 8.0, 2700.0)]
    found = race_estimate.bound(pieces)
    assert found is not None and found.km == 7.06
    assert found.vdot == pytest.approx(race_estimate.vdot(7.06, 1800))


def test_the_pieces_are_the_recent_flat_runs_and_their_laps():
    class Store:
        def strava_intake(self, activity_id):
            laps = [{"distance_m": 2000, "moving_s": 720}, {"distance_m": 1200, "moving_s": 240},
                    {"distance_m": 300, "moving_s": 120}, {"distance_m": 1200, "moving_s": 241},
                    {"distance_m": 1500, "moving_s": 540}]
            return {"distilled": {"id": activity_id, "sport": "Run", "laps": laps}}

    rows = [ledger.Row(TODAY - dt.timedelta(days=3), "threshold", "strava:7", km=6.2,
                       hours=0.52),
            ledger.Row(TODAY - dt.timedelta(days=4), "easy", km=10, hours=1.0, dplus=400,
                       dminus=400),
            ledger.Row(TODAY - dt.timedelta(weeks=20), "easy", km=10, hours=0.9)]
    found = race_estimate.pieces(Store(), rows, TODAY, CONFIG)
    assert (TODAY - dt.timedelta(days=3), 6.2, 0.52 * 3600) in found
    assert [(km, seconds) for _, km, seconds in found if km == 1.2] == [(1.2, 240), (1.2, 241)]
    assert all(on >= TODAY - dt.timedelta(weeks=8) for on, _, _ in found)
    assert all(km != 10 for _, km, _ in found)
