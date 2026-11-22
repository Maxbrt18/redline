"""The load of an easy run: equivalent minutes at a dated reference easy pace."""

import datetime as dt

import pytest

from redline.analysis import easy_load
from redline.analysis.race_pace import strava_2017

#: 6:14/km, in metres per second.
REFERENCE = 1000 / 374
DAY = dt.date(2030, 11, 11)


def _streams(minutes, pace_s, grade_pct=0.0, stop_s=0):
    """A run at a steady pace, one sample a second, on a constant grade; `stop_s` adds a
    paused watch halfway."""
    speed = 1000 / pace_s
    seconds = int(minutes * 60)
    time = list(range(seconds + 1))
    if stop_s:
        time = time[:seconds // 2] + [t + stop_s for t in time[seconds // 2:]]
    distance = [round(min(t, seconds) * speed, 1) for t in range(seconds + 1)]
    return {"t": time, "d": distance, "v": [round(speed, 2)] * len(time),
            "alt": [round(100 + d * grade_pct / 100, 1) for d in distance]}


def test_a_run_at_the_reference_pace_counts_its_own_minutes():
    run = easy_load.profile(_streams(50, 374))
    assert run.equivalent_minutes(REFERENCE) == pytest.approx(50, abs=0.2)
    assert run.flat


def test_a_slow_run_counts_less_and_a_fast_one_more():
    """The slow group run of the method: 60 minutes at 7:52/km is worth about 38."""
    slow = easy_load.profile(_streams(60, 472)).equivalent_minutes(REFERENCE)
    assert slow == pytest.approx(60 * (374 / 472) ** 2, abs=0.3)
    fast = easy_load.profile(_streams(40, 340)).equivalent_minutes(REFERENCE)
    assert fast == pytest.approx(40 * (374 / 340) ** 2, abs=0.3)


def test_a_climb_counts_at_its_flat_equivalent_speed():
    """Climbing at the speed the grade allows is the reference effort, not a slow run."""
    pace = 374 / strava_2017(6)
    climb = easy_load.profile(_streams(30, pace, grade_pct=6))
    assert not climb.flat
    assert climb.equivalent_minutes(REFERENCE) == pytest.approx(30, abs=0.5)


def test_a_paused_watch_adds_no_load():
    paused = easy_load.profile(_streams(50, 374, stop_s=300))
    assert paused.equivalent_minutes(REFERENCE) == pytest.approx(50, abs=0.2)


def test_streams_without_distance_have_no_profile():
    assert easy_load.profile({"t": [0, 1, 2]}) is None
    assert easy_load.profile({"t": [0, 1], "d": [0, 0]}) is None


def test_the_reference_reads_solo_flat_runs_of_the_weeks_before_the_day():
    flat, hilly = easy_load.profile(_streams(45, 374)), easy_load.profile(
        _streams(45, 420, grade_pct=4))
    found = easy_load.samples([
        (DAY - dt.timedelta(days=2), flat, False),
        (DAY - dt.timedelta(days=9), easy_load.profile(_streams(45, 360)), False),
        (DAY - dt.timedelta(days=16), easy_load.profile(_streams(45, 390)), False),
        (DAY - dt.timedelta(days=4), easy_load.profile(_streams(45, 300)), True),
        (DAY - dt.timedelta(days=5), easy_load.profile(_streams(45, 300)), None),
        (DAY - dt.timedelta(days=6), hilly, False)])
    assert len(found) == 3
    assert 1000 / easy_load.reference_mps(found, DAY) == pytest.approx(374, abs=1)
    # The run of the day sets no reference for itself, nor does one past the window.
    assert easy_load.reference_mps(found, DAY - dt.timedelta(days=2)) is None
    later = DAY - dt.timedelta(days=15) + dt.timedelta(weeks=easy_load.REFERENCE_WEEKS)
    assert easy_load.reference_mps(found, later) is None
