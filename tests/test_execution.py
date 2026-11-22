"""A run's execution against its typed prescription: findings, owners, verdict."""

import pytest
from test_weekly_plan import session

from redline.analysis import execution, expected_rpe
from redline.evaluation.debrief_bench import synthetic_streams
from redline.model.vocabulary import DebriefVerdict, FindingKind, FindingOwner, RpeSide, RpeSign
from redline.service import session_detail

#: 6:14/km.
REFERENCE = 374.0


def _judge(prescribed, legs, *, dplus=None, dminus=None, reference=REFERENCE, cost=None,
           reps=()):
    streams = synthetic_streams([list(leg) for leg in legs])
    splits = session_detail.km_splits(streams) or []
    return execution.judge(prescribed, splits, streams, dplus=dplus, dminus=dminus,
                           reference_pace_s=reference, cost=cost, reps=reps)


def _kinds(ruled):
    return [finding.kind for finding in ruled.findings]


FLAT = (1000, 374, 0, 85, 145)


def test_a_flat_hr_above_the_cap_reads_with_the_pace():
    """Faster than the reference easy pace: a choice. At it or slower: the body's answer."""
    capped = session(hr_zone="Z2", hr_cap_bpm=157)
    fast = _judge(capped, [FLAT, (1000, 340, 0, 88, 162), (1000, 340, 0, 88, 163)])
    assert _kinds(fast) == [FindingKind.FLAT_TOO_FAST]
    assert fast.findings[0].owner is FindingOwner.ATHLETE and fast.findings[0].kms == (2, 3)
    assert fast.verdict is DebriefVerdict.EXECUTION_GAP
    slow = _judge(capped, [FLAT, (1000, 400, 0, 85, 163), (1000, 400, 0, 85, 163)])
    assert _kinds(slow) == [FindingKind.FLAT_HR_HIGH]
    assert slow.verdict is DebriefVerdict.CONFORMING
    assert "jamais un reproche" in execution.render(slow)[1]


def test_prescribed_efforts_leave_the_flat_hr_unjudged():
    pyramid = session(structure="efforts")
    pyramid["hr_cap_bpm"] = 157  # carried from a revision written before the rule
    ruled = _judge(pyramid, [FLAT, (1000, 300, 0, 90, 168), FLAT])
    assert ruled.findings == () and ruled.verdict is DebriefVerdict.CONFORMING


def test_the_relief_is_a_route_choice_past_the_measure_noise():
    capped = session(dplus_cap_m=400, dminus_cap_m=400)
    assert _judge(capped, [FLAT], dplus=430, dminus=380).findings == ()
    ruled = _judge(capped, [FLAT], dplus=652, dminus=640)
    assert _kinds(ruled) == [FindingKind.DPLUS_OVER, FindingKind.DMINUS_OVER]
    assert {finding.owner for finding in ruled.findings} == {FindingOwner.ROUTE}
    assert "D+ 652 m pour 400 m au plus (+63 %)" in execution.render(ruled)[1]


def test_a_walked_climb_is_named_without_reproach_when_climbs_were_to_be_run():
    slow = session(climb_intent="run_slow")
    climb = (600, 900, 8, 55, 165)
    ruled = _judge(slow, [FLAT, climb, FLAT])
    assert _kinds(ruled) == [FindingKind.CLIMB_WALKED]
    assert ruled.findings[0].owner is FindingOwner.NOTE
    assert ruled.verdict is DebriefVerdict.CONFORMING
    ran = _judge(slow, [FLAT, (600, 520, 8, 80, 168), FLAT])
    assert ran.findings == ()


def test_power_hike_names_a_steep_grade_run_and_gives_the_share_walked():
    hike = session(climb_intent="power_hike", power_hike_from_pct=10)
    walked = _judge(hike, [FLAT, (500, 1000, 14, 55, 160), FLAT])
    assert _kinds(walked) == [FindingKind.STEEP_WALKED_SHARE]
    assert walked.findings[0].value == pytest.approx(1.0, abs=0.05)
    run = _judge(hike, [FLAT, (500, 600, 14, 82, 172), FLAT])
    assert _kinds(run) == [FindingKind.STEEP_WALKED_SHARE, FindingKind.STEEP_RUN]
    assert run.verdict is DebriefVerdict.EXECUTION_GAP


def test_held_descents_faster_than_the_flat_easy_pace_are_an_execution_gap():
    held = session(descent_intent="held")
    ruled = _judge(held, [FLAT, (1000, 330, -5, 88, 140), FLAT])
    assert _kinds(ruled) == [FindingKind.DESCENT_NOT_HELD]
    assert _judge(held, [FLAT, (1000, 420, -5, 82, 135), FLAT]).findings == ()


def test_without_a_prescription_or_a_reading_nothing_is_ruled():
    assert execution.judge(None, [], None, dplus=None, dminus=None,
                           reference_pace_s=None).verdict is DebriefVerdict.UNDETERMINED
    assert execution.judge(session(), [], None, dplus=None, dminus=None,
                           reference_pace_s=None).verdict is DebriefVerdict.UNDETERMINED


# ------------------------------------------------------------------ the cost


def _expect(expected: float, low: int, high: int) -> expected_rpe.Expectation:
    return expected_rpe.Expectation(expected, low, high, sessions=7, residual=0.3)


def _cost(declared, run=(4.3, 3, 5), prescribed=None, **extra) -> execution.Cost:
    return execution.Cost(declared, _expect(*run),
                          None if prescribed is None else _expect(*prescribed), **extra)


def test_an_rpe_within_its_range_says_nothing():
    ruled = _judge(session(), [FLAT], cost=_cost(5))
    assert ruled.findings == () and ruled.verdict is DebriefVerdict.CONFORMING
    assert execution.render(ruled, _cost(5))[1] == (
        "RPE attendu pour la séance courue : 3-5 (7 séances de calibration)")


def test_an_rpe_out_of_range_without_a_second_sign_is_kept_not_said():
    ruled = _judge(session(), [FLAT], cost=_cost(7))
    assert _kinds(ruled) == [FindingKind.RPE_ABOVE]
    assert ruled.findings[0].signs == () and ruled.verdict is DebriefVerdict.CONFORMING
    assert ruled.findings[0].as_json()["signs"] == []
    assert "sans second indice : n'en parle pas" in execution.render(ruled)[-1]


def test_a_costlier_run_followed_as_prescribed_is_adjusted_without_reproach():
    ruled = _judge(session(), [FLAT], cost=_cost(7, signs=(RpeSign.POOR_NIGHT,)))
    assert ruled.findings[0].signs == (RpeSign.POOR_NIGHT,)
    assert ruled.verdict is DebriefVerdict.CONFORMING
    line = execution.render(ruled)[-1]
    assert "coûté plus que prévu" in line and "mauvaise nuit" in line


def test_a_run_run_above_its_prescription_gets_the_correction_never_the_rpe():
    """A long run prescribed at the easy pace, run as a race: its RPE is normal for what
    was run, the choice of running it so is the gap."""
    ruled = _judge(session(), [FLAT], cost=_cost(6, run=(5.6, 5, 7), prescribed=(4.0, 3, 5)))
    assert _kinds(ruled) == [FindingKind.RUN_ABOVE_PRESCRIPTION]
    assert ruled.findings[0].owner is FindingOwner.ATHLETE
    assert ruled.verdict is DebriefVerdict.EXECUTION_GAP
    assert "jamais le chiffre" in execution.render(ruled)[-1]
    above = _judge(session(), [FLAT], cost=_cost(8, run=(5.6, 5, 7), prescribed=(4.0, 3, 5)))
    assert above.findings[-1].signs == (RpeSign.EXECUTION,)
    assert "la correction porte sur le choix" in execution.render(above)[-1]
    close = _judge(session(), [FLAT], cost=_cost(5, run=(4.6, 4, 6), prescribed=(4.0, 3, 5)))
    assert close.findings == (), "a dose expected within the prescribed range is no gap"


def test_two_sessions_out_of_range_on_the_same_side_make_a_pattern():
    ruled = _judge(session(), [FLAT], cost=_cost(1, previous=RpeSide.BELOW))
    assert _kinds(ruled) == [FindingKind.RPE_BELOW]
    assert ruled.findings[0].signs == (RpeSign.PATTERN,)
    assert "une seule question" in execution.render(ruled)[-1]
    lower = _judge(session(), [FLAT], cost=_cost(1, signs=(RpeSign.HR_AT_PACE,)))
    assert "jamais de hausse de dose" in execution.render(lower)[-1]


def test_a_flat_hr_high_is_a_second_sign():
    capped = session(hr_zone="Z2", hr_cap_bpm=157)
    ruled = _judge(capped, [FLAT, (1000, 400, 0, 85, 163), (1000, 400, 0, 85, 163)],
                   cost=_cost(7))
    assert ruled.findings[-1].signs == (RpeSign.FLAT_HR_HIGH,)


def test_repetitions_faster_than_their_pace_are_an_execution_gap():
    threshold = session(workout="threshold", reps=3, rep_seconds=420, rep_pace_s=275)
    held = [{"time_s": 420, "dist_m": 1530}, {"time_s": 420, "dist_m": 1525},
            {"time_s": 420, "dist_m": 1540}]
    assert _judge(threshold, [FLAT], reps=held).findings == ()
    pushed = [*held[:2], {"time_s": 420, "dist_m": 1650}]
    ruled = _judge(threshold, [FLAT], reps=pushed)
    assert _kinds(ruled) == [FindingKind.REPS_FASTER]
    assert ruled.findings[0].kms == (3,) and ruled.verdict is DebriefVerdict.EXECUTION_GAP
    assert "R3 à 4:15/km pour 4:35/km" in execution.render(ruled)[-1]
    assert _judge(session(workout="threshold"), [FLAT], reps=pushed).findings == (), \
        "without a typed pace, the repetitions are compared within the session only"
