"""A run's execution against its typed prescription: findings, owners, verdict."""

import pytest
from test_weekly_plan import session

from redline.analysis import execution
from redline.evaluation.debrief_bench import synthetic_streams
from redline.model.vocabulary import DebriefVerdict, FindingKind, FindingOwner
from redline.service import session_detail

#: 6:14/km.
REFERENCE = 374.0


def _judge(prescribed, legs, *, dplus=None, dminus=None, reference=REFERENCE):
    streams = synthetic_streams([list(leg) for leg in legs])
    splits = session_detail.km_splits(streams) or []
    return execution.judge(prescribed, splits, streams, dplus=dplus, dminus=dminus,
                           reference_pace_s=reference)


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
