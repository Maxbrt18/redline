"""Critical speed and D′: a straight line through the flat all-out efforts of 2 to 20 min."""

from __future__ import annotations

import datetime as dt

import pytest

from redline.analysis import critical_speed
from redline.model.efforts import Effort
from redline.model.vocabulary import EffortKind, EffortTerrain

TODAY = dt.date(2030, 11, 27)
CONFIG = critical_speed.Settings(weeks=12, first_threshold_pct=82.3, threshold_low_pct=90,
                                 threshold_high_pct=97)
CS, D_PRIME = 4.2, 180.0


def effort(seconds: float, *, days_ago: int = 10, terrain=EffortTerrain.FLAT,
           noise: float = 0.0) -> Effort:
    km = (CS * seconds + D_PRIME + noise) / 1000
    return Effort(f"e{seconds}", TODAY - dt.timedelta(days=days_ago), km, seconds, terrain,
                  EffortKind.TEST)


def test_two_efforts_give_the_line_and_no_error():
    found = critical_speed.fit([effort(240), effort(840)], TODAY, CONFIG)
    assert found is not None and found.speed_se is None
    assert found.speed_mps == pytest.approx(CS) and found.d_prime_m == pytest.approx(D_PRIME)
    assert found.pace_s == pytest.approx(1000 / CS)


def test_three_efforts_give_the_standard_error_of_cs():
    found = critical_speed.fit([effort(240, noise=4), effort(500, noise=-6), effort(840, noise=3)],
                               TODAY, CONFIG)
    assert found is not None and found.speed_se is not None and found.speed_se > 0
    assert found.speed_mps == pytest.approx(CS, abs=0.05)


@pytest.mark.parametrize("efforts", [
    [effort(240), effort(400)],                               # spread under 5 min
    [effort(240), effort(840, terrain=EffortTerrain.HILLY)],  # hilly
    [effort(240), effort(840, days_ago=7 * 13)],              # out of the window
    [effort(60), effort(840)],                                # under 2 min
    [effort(240), effort(30 * 60)],                           # over 20 min
])
def test_efforts_outside_the_model_give_no_fit(efforts):
    assert critical_speed.fit(efforts, TODAY, CONFIG) is None


def test_the_domains_read_as_shares_of_cs():
    found = critical_speed.fit([effort(240), effort(840)], TODAY, CONFIG)
    assert found is not None
    assert found.pace_at(82.3) > found.pace_at(90) > found.pace_at(97) > found.pace_s
