"""A road race's time from a recent race, by Daniels' VDOT equivalence."""

from __future__ import annotations

import datetime as dt

import pytest

from redline.analysis import ledger, race_estimate

TODAY = dt.date(2030, 11, 27)
HALF_KM = 21.0975


@pytest.mark.parametrize(("km", "seconds"), [
    (5, 19 * 60 + 57), (10, 41 * 60 + 21), (HALF_KM, 3600 + 31 * 60 + 35),
    (42.195, 3 * 3600 + 10 * 60 + 49)])
def test_the_published_table_reads_as_one_vdot(km, seconds):
    """Daniels' table, VDOT 50: 5 km 19:57, 10 km 41:21, half 1:31:35, marathon 3:10:49."""
    assert race_estimate.vdot(km, seconds) == pytest.approx(50, abs=0.1)


def test_the_time_at_another_distance_keeps_the_vdot():
    assert race_estimate.time_for(50, HALF_KM) == pytest.approx(3600 + 31 * 60 + 35, abs=10)


def race(days_ago: int, km: float, minutes: float, dplus: float = 0.0) -> ledger.Row:
    return ledger.Row(TODAY - dt.timedelta(days=days_ago), "race", km=km, hours=minutes / 60,
                      dplus=dplus, dminus=dplus)


def test_the_estimate_comes_from_the_best_recent_flat_race():
    rows = [race(30, 10, 46), race(60, 10, 44), race(20, 5, 23),
            ledger.Row(TODAY - dt.timedelta(days=10), "threshold", km=10, hours=0.6)]
    found = race_estimate.estimate(rows, TODAY, HALF_KM)
    assert found is not None and found.source_km == 10 and found.source_s == 44 * 60
    assert found.vdot == pytest.approx(race_estimate.vdot(10, 44 * 60))
    assert 3600 + 35 * 60 < found.time_s < 3600 + 40 * 60


def test_a_hilly_old_or_short_race_gives_no_estimate():
    rows = [race(30, 15, 70, dplus=400), race(30 * 7, 10, 44), race(10, 2, 7)]
    assert race_estimate.estimate(rows, TODAY, HALF_KM) is None
