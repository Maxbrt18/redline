"""Heat and night: calibrated on the athlete's RPE residuals, applied once established."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from redline.analysis import daylight, execution, expected_rpe, ledger
from redline.analysis.expected_rpe import Conditions
from redline.model.vocabulary import Condition

SETTINGS = expected_rpe.Settings(26, 3, 5, 1.0, 1.5, 2.0)
LYON = (45.76, 4.84)
PARIS = ZoneInfo("Europe/Paris")


def effect(found, condition):
    return next(item for item in found if item.condition is condition)


def test_a_condition_is_applied_once_enough_runs_establish_it():
    night = [(Conditions(night=True), 1.0 + 0.1 * (index % 3)) for index in range(6)]
    day = [(Conditions(night=False), 0.1 * (index % 3 - 1)) for index in range(10)]
    found = effect(expected_rpe.calibrate([*night, *day], 5), Condition.NIGHT)
    assert found.supported and found.value == pytest.approx(1.11, abs=0.01)
    assert found.sessions == 6
    few = effect(expected_rpe.calibrate([*night[:3], *day], 5), Condition.NIGHT)
    assert not few.supported and few.value == 0, "three night runs establish nothing"


def test_noise_is_not_an_effect():
    noisy = [(Conditions(night=index % 2 == 0), (-1) ** (index // 2) * 0.8) for index in range(16)]
    assert not effect(expected_rpe.calibrate(noisy, 5), Condition.NIGHT).supported
    assert {item.condition for item in expected_rpe.calibrate(noisy, 5)} == {
        Condition.HEAT, Condition.NIGHT}, "a group is no condition"


def test_heat_counts_per_wbgt_degree_above_the_optimum():
    points = [(Conditions(heat_c=float(degrees)), 0.15 * degrees + 0.05 * (degrees % 2))
              for degrees in (0, 0, 0, 0, 0, 2, 4, 6, 8, 10, 12)]
    found = effect(expected_rpe.calibrate(points, 5), Condition.HEAT)
    assert found.supported and found.value == pytest.approx(0.15, abs=0.02)
    assert expected_rpe.heat_of(ledger.Row(dt.date(2030, 7, 1), "easy", wbgt=8.0)) == 0
    assert expected_rpe.heat_of(ledger.Row(dt.date(2030, 7, 1), "easy", wbgt=14.5)) == 4.5


def test_an_established_effect_shifts_the_expectation_of_a_run_that_met_it():
    start = dt.date(2030, 3, 4)
    rows = [ledger.Row(start + dt.timedelta(days=index), "easy", km=6 + index % 4, hours=0.7,
                       rpe=3.0 + (1.0 if index % 3 == 0 else 0.0) + 0.1 * (index % 2))
            for index in range(24)]
    history = expected_rpe.History(
        rows, lambda row: None, lambda day: None, SETTINGS,
        lambda row: Conditions(night=(row.date - start).days % 3 == 0))
    last_night = rows[21]
    found = history.expectation(last_night)
    assert found is not None and found.adjustments
    (condition, value), = found.adjustments
    assert condition is Condition.NIGHT and value == pytest.approx(1.0, abs=0.2)
    assert history.expectation(rows[22]).adjustments == (), "a day run is not shifted"


def test_a_run_is_dark_below_civil_twilight():
    winter = dt.datetime(2030, 11, 14, 21, 0)
    assert daylight.dark_share(*LYON, winter, 2 * 3600, PARIS) == 1.0
    summer = dt.datetime(2030, 6, 21, 19, 0)
    assert daylight.dark_share(*LYON, summer, 3600, PARIS) == 0.0
    assert daylight.elevation(*LYON, dt.datetime(2030, 12, 21, 12, 41, tzinfo=PARIS)) == \
        pytest.approx(90 - 45.76 - 23.44, abs=0.5)


def test_the_debrief_names_what_a_condition_added():
    run = expected_rpe.Expectation(5.6, 5, 7, 12, 0.4, ((Condition.HEAT, 0.6),))
    lines = execution.render(execution.Execution(execution.DebriefVerdict.CONFORMING, ()),
                             execution.Cost(declared=6.0, run=run))
    assert "dont chaleur +0,6, calibré sur ton historique" in lines[1]
