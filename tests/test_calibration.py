"""A calibration is adopted on a declared all-out effort, and on nothing weaker."""

from __future__ import annotations

import dataclasses
import datetime as dt

from redline.analysis import calibration, race_estimate
from redline.model.efforts import Break, Effort
from redline.model.vocabulary import (
    CalibrationConfidence,
    CalibrationSource,
    Decider,
    EffortKind,
    EffortTerrain,
    RetestReason,
)

TODAY = dt.date(2030, 11, 27)
CONFIG = calibration.settings()


def effort(key, days_ago, km, minutes, kind=EffortKind.TEST, terrain=EffortTerrain.FLAT):
    return Effort(key, TODAY - dt.timedelta(days=days_ago), km, minutes * 60, terrain, kind)


#: A critical-speed test (1,200 m then 3,600 m) and a 10 km race, all recent.
TEST = [effort("t1200", 20, 1.2, 4), effort("t3600", 20, 3.6, 14)]
TEN = effort("ten", 40, 10, 41, kind=EffortKind.RACE)


def test_the_first_calibration_reads_every_usable_effort():
    found = calibration.adoption(None, [*TEST, TEN], TODAY, CONFIG)
    assert (found.source, found.confidence, found.decided_by) == (
        CalibrationSource.INITIAL, CalibrationConfidence.MEASURED, Decider.RULE)
    assert found.adopted_on == TODAY and found.evidence == ("t1200", "t3600", "ten")
    assert found.cs_mps is not None and found.d_prime_m is not None
    assert found.cs_se_mps is None, "two efforts leave no residual"
    assert found.vdot is not None and found.vdot_sd is not None


def test_a_first_calibration_on_old_efforts_is_provisional():
    old = effort("half", 200, 21.1, 95, kind=EffortKind.RACE)
    found = calibration.adoption(None, [old], TODAY, CONFIG)
    assert found.confidence == CalibrationConfidence.PROVISIONAL
    assert found.cs_mps is None, "no short effort, no critical speed"


def test_only_a_declared_effort_newer_than_the_evidence_adopts_again():
    current = calibration.adoption(None, [TEN], TODAY - dt.timedelta(days=30), CONFIG)
    assert calibration.adoption(current, [TEN], TODAY, CONFIG) is None
    older = effort("five", 60, 5, 19, kind=EffortKind.RACE)
    assert calibration.adoption(current, [older, TEN], TODAY, CONFIG) is None
    tested = calibration.adoption(current, [TEN, *TEST], TODAY, CONFIG)
    assert tested.source == CalibrationSource.TEST and "t3600" in tested.evidence
    raced = calibration.adoption(current, [TEN, effort("half", 5, 21.1, 92,
                                                       kind=EffortKind.RACE)], TODAY, CONFIG)
    assert raced.source == CalibrationSource.RACE


def test_a_hilly_effort_adopts_nothing():
    hilly = effort("trail", 10, 12, 70, kind=EffortKind.RACE, terrain=EffortTerrain.HILLY)
    assert calibration.adoption(None, [hilly], TODAY, CONFIG) is None


def test_measured_heart_rate_thresholds_carry_over_to_the_next_calibration():
    current = dataclasses.replace(calibration.adoption(None, [TEN], TODAY, CONFIG),
                                  hr_first_threshold_bpm=152)
    found = calibration.adoption(current, [TEN, *TEST], TODAY, CONFIG)
    assert found.hr_first_threshold_bpm == 152


def bound(vdot, on):
    return race_estimate.Bound(on, 5.0, 1200.0, vdot)


def test_a_session_beyond_the_band_proposes_a_test_and_adopts_nothing():
    current = calibration.adoption(None, [TEN], TODAY, CONFIG)
    top = current.vdot + CONFIG.band_sd * current.vdot_sd
    within = calibration.retest_proposal(current, [TEN], [], bound(top - 0.1, TODAY), CONFIG)
    assert within is None
    found = calibration.retest_proposal(current, [TEN], [], bound(top + 1, TODAY), CONFIG)
    assert found.reason == RetestReason.SESSIONS_ABOVE and found.band_top == round(top, 1)
    assert calibration.adoption(current, [TEN], TODAY, CONFIG) is None


def test_a_declared_break_after_the_newest_effort_proposes_a_test():
    current = calibration.adoption(None, [TEN], TODAY, CONFIG)
    short = Break("cold", TEN.on + dt.timedelta(days=2), TEN.on + dt.timedelta(days=4))
    assert calibration.retest_proposal(current, [TEN], [short], None, CONFIG) is None
    long = Break("shin", TEN.on + dt.timedelta(days=2), TEN.on + dt.timedelta(days=30))
    found = calibration.retest_proposal(current, [TEN], [long], None, CONFIG)
    assert (found.reason, found.on) == (RetestReason.BREAK, long.ends_on)
    assert calibration.retest_proposal(None, [TEN], [long], None, CONFIG) is None
