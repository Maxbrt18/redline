"""A preparation holds while the rest of its trame is reachable by the engine's rules."""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

from redline.model import cycle_templates, hypotheses, iso_week
from redline.model import plan_generator as generator
from redline.model.hypotheses import ClosedWeek, Fallen, Observed
from redline.model.progression import Dose
from redline.model.vocabulary import (
    GeneralLevel,
    HypothesisKind,
    HypothesisOutcome,
    PlanPhase,
    RaceTerrain,
)

TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)
HALF = TEMPLATES.preparation_for(21.1, RaceTerrain.ROAD)
CALIBRATED_ON = dt.date(2030, 12, 1)
GOAL = generator.Goal(dt.date(2031, 4, 6), 21.1, RaceTerrain.ROAD, 5400)
INPUTS = generator.Inputs("2031-W02", 40, 14, 50, 10, 10, minutes_per_week=360,
                          easy_pace_s=330, calibrated=True, calibration_on=CALIBRATED_ON,
                          lineages=(("threshold", Dose.of(reps=3, rep_seconds=480,
                                                          recovery_seconds=90)),))
#: A Wednesday of the specific block, two build weeks before the peak (2031-W12).
SPECIFIC_DAY = dt.date(2031, 3, 5)
#: The threshold dose the trame plans in 2031-W10.
PLANNED_THRESHOLD = Dose.of(reps=3, rep_seconds=600, recovery_seconds=60)


def _trame(inputs: generator.Inputs = INPUTS, goal: generator.Goal = GOAL) -> generator.Trame:
    assert HALF is not None
    return generator.preparation(HALF, goal, generator.candidate_levers(HALF)["balanced"],
                                 inputs, candidate="balanced")


def _ran(trame: generator.Trame, today: dt.date) -> tuple[ClosedWeek, ...]:
    """Every closed week of the trame run as planned, deloads and taper left out, newest
    first."""
    current = iso_week.of(today)
    return tuple(ClosedWeek(week.label, week.volume_km, week.long_run_km)
                 for week in reversed(trame.weeks)
                 if week.label < current and not week.deload and week.phase != PlanPhase.TAPER)


def _weeks(*ran: tuple[str, float, float]) -> tuple[ClosedWeek, ...]:
    return tuple(ClosedWeek(*week) for week in ran)


def _observed(trame: generator.Trame, today: dt.date = SPECIFIC_DAY, **changes) -> Observed:
    base = Observed(today, _ran(trame, today), (("threshold", PLANNED_THRESHOLD),), 360,
                    CALIBRATED_ON, GOAL)
    return dataclasses.replace(base, **changes)


def _kinds(review: hypotheses.Review) -> set[HypothesisKind]:
    return {found.kind for found in review.fallen}


def test_a_preparation_run_as_planned_holds():
    trame = _trame()
    review = hypotheses.check(trame, _observed(trame), TEMPLATES)
    assert review == hypotheses.HOLDS


def test_only_a_preparation_is_checked():
    general = generator.general(TEMPLATES.general_at(GeneralLevel.MAINTAIN), INPUTS)
    assert hypotheses.check(general, _observed(_trame(), weeks=()), TEMPLATES) \
        == hypotheses.HOLDS


def test_one_short_week_never_drops_the_volume():
    trame = _trame()
    weeks = _weeks(("2031-W09", 40.0, 16.0), ("2031-W08", 48.4, 14.5))
    review = hypotheses.check(trame, _observed(trame, weeks=weeks), TEMPLATES)
    assert HypothesisKind.VOLUME_HELD not in _kinds(review)


def test_two_short_weeks_from_which_the_ramp_misses_the_peak_drop_the_volume():
    trame = _trame()
    weeks = _weeks(("2031-W09", 40.0, 16.0), ("2031-W08", 40.0, 14.5))
    review = hypotheses.check(trame, _observed(trame, weeks=weeks), TEMPLATES)
    assert review.outcome == HypothesisOutcome.REPLAN
    # From 2031-W09, two build weeks climb to the peak of 2031-W12, the deload aside.
    assert review.fallen == (Fallen(HypothesisKind.VOLUME_HELD, round(40 * 1.1 ** 2, 1),
                                    58.6),)


def test_two_short_weeks_the_ramp_still_lifts_to_the_peak_hold():
    trame = _trame()
    weeks = _weeks(("2031-W09", 50.0, 16.0), ("2031-W08", 45.0, 14.5))
    review = hypotheses.check(trame, _observed(trame, weeks=weeks), TEMPLATES)
    assert review == hypotheses.HOLDS


def test_the_volume_is_not_read_before_two_weeks_of_the_version_have_closed():
    trame = _trame()
    review = hypotheses.check(trame, _observed(trame, dt.date(2031, 1, 15),
                                               weeks=_weeks(("2031-W02", 10.0, 5.0),
                                                            ("2031-W01", 10.0, 5.0))),
                              TEMPLATES)
    assert review == hypotheses.HOLDS


def test_once_the_peak_has_passed_the_volume_is_not_checked():
    trame = _trame()
    day = dt.date(2031, 3, 26)
    weeks = _weeks(("2031-W12", 20.0, 8.0), ("2031-W10", 20.0, 8.0))
    review = hypotheses.check(trame, _observed(trame, day, weeks=weeks), TEMPLATES)
    assert not _kinds(review) & {HypothesisKind.VOLUME_HELD, HypothesisKind.LONG_RUN_HELD}


def test_two_weeks_whose_long_runs_the_step_cannot_lift_drop_the_long_run():
    trame = _trame()
    short = _weeks(("2031-W09", 53.2, 12.0), ("2031-W08", 48.4, 11.0))
    review = hypotheses.check(trame, _observed(trame, weeks=short), TEMPLATES)
    # From 2031-W09, two build weeks lift the long run to the 17.6 km of 2031-W12.
    assert review.fallen == (Fallen(HypothesisKind.LONG_RUN_HELD, round(12 * 1.1 ** 2, 1),
                                    17.6),)
    one = _weeks(("2031-W09", 53.2, 12.0), ("2031-W08", 48.4, 14.5))
    assert hypotheses.check(trame, _observed(trame, weeks=one), TEMPLATES) == hypotheses.HOLDS


def test_a_lineage_too_far_behind_its_last_planned_dose_drops_its_position():
    trame = _trame()
    behind = Dose.of(reps=4, rep_seconds=480, recovery_seconds=60)
    review = hypotheses.check(trame, _observed(trame, lineages=(("threshold", behind),)),
                              TEMPLATES)
    assert review.outcome == HypothesisOutcome.REPLAN
    # 2031-W10 and 2031-W12 are left, one step between them; a rep and 120 s of a 300 s
    # step are behind.
    assert review.fallen == (Fallen(HypothesisKind.LINEAGE_POSITION, 1.4, 1.0, "threshold"),)


def test_a_lineage_one_step_behind_with_one_exposure_to_climb_holds():
    trame = _trame()
    one = Dose.of(reps=4, rep_seconds=600, recovery_seconds=60)
    review = hypotheses.check(trame, _observed(trame, lineages=(("threshold", one),)),
                              TEMPLATES)
    assert review == hypotheses.HOLDS


def test_a_lineage_ahead_of_its_trame_holds():
    trame = _trame()
    ahead = Dose.of(reps=3, rep_seconds=900, recovery_seconds=60)
    review = hypotheses.check(trame, _observed(trame, lineages=(("threshold", ahead),)),
                              TEMPLATES)
    assert review == hypotheses.HOLDS


def test_fewer_minutes_a_week_ahead_no_longer_fits_drop_the_availability():
    trame = _trame()
    review = hypotheses.check(trame, _observed(trame, minutes_per_week=300), TEMPLATES)
    assert review.outcome == HypothesisOutcome.REPLAN
    assert review.fallen == (Fallen(HypothesisKind.AVAILABILITY, 300.0, 360.0),)


def test_other_minutes_every_week_ahead_still_fits_hold():
    trame = _trame()
    review = hypotheses.check(trame, _observed(trame, minutes_per_week=340), TEMPLATES)
    assert review == hypotheses.HOLDS


def test_more_time_lifts_a_peak_the_time_capped():
    capped = _trame(dataclasses.replace(INPUTS, minutes_per_week=250))
    assert any(risk.kind == generator.TrameRisk.TIME_BOUND for risk in capped.risks)
    held = hypotheses.check(capped, _observed(capped, minutes_per_week=250), TEMPLATES)
    lifted = hypotheses.check(capped, _observed(capped, minutes_per_week=None), TEMPLATES)
    assert held == hypotheses.HOLDS
    assert _kinds(lifted) == {HypothesisKind.AVAILABILITY}


def test_a_race_moved_drops_the_goal():
    trame = _trame()
    moved = dataclasses.replace(GOAL, on=dt.date(2031, 4, 13))
    review = hypotheses.check(trame, _observed(trame, goal=moved), TEMPLATES)
    assert review.outcome == HypothesisOutcome.REPLAN
    assert review.fallen == (Fallen(HypothesisKind.GOAL, field="on"),)


def test_a_new_goal_time_or_calibration_only_renders_the_paces_again():
    trame = _trame()
    review = hypotheses.check(trame, _observed(
        trame, goal=dataclasses.replace(GOAL, goal_time_s=5300),
        calibration_on=dt.date(2031, 3, 1)), TEMPLATES)
    assert review.outcome == HypothesisOutcome.RECALIBRATE
    assert _kinds(review) == {HypothesisKind.GOAL_TIME, HypothesisKind.CALIBRATION}
    assert review.replanning == ()


def test_a_version_that_froze_no_terrain_compares_neither_terrain_nor_goal_time():
    trame = _trame(goal=generator.Goal(GOAL.on, GOAL.distance_km))
    review = hypotheses.check(trame, _observed(trame), TEMPLATES)
    assert review == hypotheses.HOLDS


def test_two_causes_found_the_same_day_ask_for_one_review():
    trame = _trame()
    version = uuid.uuid4()
    review = hypotheses.check(trame, _observed(
        trame, minutes_per_week=300, goal=dataclasses.replace(GOAL, on=dt.date(2031, 4, 13)),
        calibration_on=dt.date(2031, 3, 1)), TEMPLATES)
    assert review.replanning == (HypothesisKind.AVAILABILITY, HypothesisKind.GOAL)
    assert hypotheses.review_key(version, review) == f"plan-review:{version}:availability+goal"


def test_nothing_that_calls_for_a_new_version_asks_for_no_review():
    assert hypotheses.review_key(uuid.uuid4(), hypotheses.HOLDS) is None


def test_first_times_left_with_no_room_on_the_build_weeks_left_replan():
    goal = dataclasses.replace(GOAL, firsts=("night",))
    trame = _trame(goal=goal)
    assert any(key.role == "first" for key in trame.keys)
    taper_day = dt.date(2031, 3, 26)
    pending = hypotheses.check(trame, _observed(trame, taper_day, goal=goal), TEMPLATES)
    assert pending.fallen == (Fallen(HypothesisKind.FIRSTS, 1.0, 0.0),)
    done = hypotheses.check(trame, _observed(trame, taper_day, goal=goal,
                                             firsts_done=frozenset({"night"})), TEMPLATES)
    assert done == hypotheses.HOLDS
    assert hypotheses.check(trame, _observed(trame, goal=goal), TEMPLATES) \
        == hypotheses.HOLDS, "build weeks are left: the first slides"
    changed = hypotheses.check(trame, _observed(trame, goal=GOAL), TEMPLATES)
    assert changed.fallen == (Fallen(HypothesisKind.GOAL, field="firsts"),)
