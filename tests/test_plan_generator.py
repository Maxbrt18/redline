"""The trame of a cycle, generated whole: what every engine template promises, and the
rules one by one."""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools

import pytest

from redline.model import cycle_templates, iso_week
from redline.model import plan_generator as generator
from redline.model.plan_generator import Goal, Inputs, TrameRisk
from redline.model.progression import Dose
from redline.model.vocabulary import (
    CycleKind,
    GeneralLevel,
    KeyRole,
    PlanPhase,
    RaceTerrain,
)

TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)
RACE = dt.date(2031, 4, 6)
GOALS = ((10, RaceTerrain.ROAD), (21.1, RaceTerrain.ROAD), (42.2, RaceTerrain.ROAD),
         (30, RaceTerrain.TRAIL), (80, RaceTerrain.TRAIL))
HALF = TEMPLATES.preparation_for(21.1, RaceTerrain.ROAD)
INPUTS = Inputs("2030-W30", start_km=40, start_long_km=15, held_km=50, ramp_max_pct=10,
                long_step_max_pct=10, easy_pace_s=360, calibrated=True)
#: A trame's figures are rounded to 0.1 km.
ROUNDING_KM = 0.1


def _trames():
    for (distance, terrain), start, origin in itertools.product(
            GOALS, ((25, 10), (40, 15), (60, 22)), ("2030-W30", "2031-W05")):
        template = TEMPLATES.preparation_for(distance, terrain)
        inputs = dataclasses.replace(INPUTS, origin=origin, start_km=start[0],
                                     start_long_km=start[1])
        for trame in generator.candidates(template, Goal(RACE, distance), inputs):
            yield pytest.param((template, trame),
                               id=f"{template.slug}-{trame.candidate}-{start[0]}km-{origin}")


@pytest.fixture(params=list(_trames()))
def generated(request):
    return request.param


def _builds(trame):
    return [week for week in trame.weeks if week.phase in generator.BUILD_PHASES
            and not week.deload]


# ------------------------------------------------------------------ what every template promises


def test_the_trame_ends_with_the_taper_on_the_race_week(generated):
    template, trame = generated
    assert trame.weeks[-1].label == iso_week.of(RACE)
    assert [week.phase for week in trame.weeks[-template.taper_weeks:]] == \
        [PlanPhase.TAPER] * template.taper_weeks
    ranks = [generator.BUILD_PHASES.index(week.phase) for week in trame.weeks
             if week.phase in generator.BUILD_PHASES]
    assert ranks == sorted(ranks)


def test_no_build_week_climbs_faster_than_the_ramp(generated):
    _, trame = generated
    for before, week in itertools.pairwise(_builds(trame)):
        assert week.volume_km <= before.volume_km * 1.1 + ROUNDING_KM, week.label
        assert week.long_run_km <= before.long_run_km * 1.1 + ROUNDING_KM, week.label


def test_the_peak_never_exceeds_the_templates_share_of_the_volume_held(generated):
    template, trame = generated
    held = max(trame.hypotheses.held_km, trame.hypotheses.start_km)
    assert max(week.volume_km for week in trame.weeks) <= \
        held * template.peak_pct_of_held / 100 + ROUNDING_KM
    assert max(week.long_run_km for week in trame.weeks) <= template.long_run_km_max


def test_volume_and_doses_never_climb_in_the_same_week(generated):
    _, trame = generated
    doses = {}
    for before, week in itertools.pairwise(trame.weeks):
        if week.phase not in generator.BUILD_PHASES or week.deload:
            continue
        climbed = any(doses.get(key.workout) not in (None, key.dose)
                      for key in trame.keys if key.week == week.label)
        if week.volume_km > before.volume_km + ROUNDING_KM / 2 and not before.deload:
            assert not climbed, week.label
        doses.update({key.workout: key.dose for key in trame.keys if key.week == week.label})


def test_the_taper_cuts_progressively_within_its_band(generated):
    template, trame = generated
    taper = [week for week in trame.weeks if week.phase == PlanPhase.TAPER]
    peak = _builds(trame)[-1].volume_km
    cuts = [1 - week.volume_km / peak for week in taper]
    assert cuts == sorted(cuts), "the cut deepens towards the race"
    mean = sum(cuts) / len(cuts) * 100
    assert template.taper_cut_pct.low - 1 <= mean <= template.taper_cut_pct.high + 1


def test_no_deload_falls_on_the_week_before_the_taper(generated):
    _, trame = generated
    taper = next(index for index, week in enumerate(trame.weeks)
                 if week.phase == PlanPhase.TAPER)
    assert not trame.weeks[taper - 1].deload


def test_a_flagship_session_never_falls_in_a_deload(generated):
    _, trame = generated
    for key in trame.keys:
        if key.role == KeyRole.FLAGSHIP:
            assert not trame.week(key.week).deload, key.week


def test_the_same_inputs_give_the_same_trame(generated):
    template, trame = generated
    assert generator.preparation(template, trame.goal, trame.levers, trame.hypotheses,
                                 candidate=trame.candidate) == trame


# ------------------------------------------------------------------ the rules one by one


def _balanced(inputs=INPUTS, template=HALF):
    return generator.preparation(template, Goal(RACE, 21.1),
                                 generator.candidate_levers(template)["balanced"], inputs,
                                 candidate="balanced")


def test_the_window_starts_as_late_as_its_levers_allow_never_before_the_origin():
    trame = _balanced()
    assert len(trame.weeks) == 2 + 5 + 6, "taper, the balanced specific block, the longest development"
    late = _balanced(dataclasses.replace(INPUTS, origin="2031-W08"))
    assert late.weeks[0].label == "2031-W08"
    assert TrameRisk.WINDOW_SHORT in {risk.kind for risk in late.risks}


def test_a_key_session_moves_one_variable_by_one_step_and_holds_in_a_deload():
    trame = _balanced()
    threshold = [key for key in trame.keys if key.workout == "threshold"
                 and trame.week(key.week).phase == PlanPhase.DEVELOPMENT]
    for before, key in itertools.pairwise(threshold):
        moved = [name for name, value in key.dose.values if before.dose.get(name) != value]
        assert len(moved) <= 1, (before.dose, key.dose)
        if trame.week(key.week).deload:
            assert moved == []


def test_a_lineage_s_position_starts_the_first_phase_of_its_workout():
    held = Dose.of(reps=4, rep_seconds=540, recovery_seconds=90)
    trame = _balanced(dataclasses.replace(INPUTS, lineages=(("threshold", held),)))
    first = next(key for key in trame.keys if key.workout == "threshold")
    assert first.dose == held


def test_an_arrival_out_of_reach_is_a_risk_not_a_steeper_slope():
    trame = generator.preparation(HALF, Goal(RACE, 21.1),
                                  generator.candidate_levers(HALF)["cautious"], INPUTS)
    unreached = [risk for risk in trame.risks if risk.kind == TrameRisk.ARRIVAL_UNREACHED]
    assert unreached and all(risk.phase is not None for risk in unreached)


def test_a_missing_or_stale_calibration_opens_the_window_with_a_test():
    trame = _balanced(dataclasses.replace(INPUTS, calibrated=False))
    first = [key for key in trame.keys if key.week == trame.weeks[0].label]
    assert first[0].role == KeyRole.TEST and first[0].dose is None
    assert all(key.role != KeyRole.TEST for key in _balanced().keys)


def test_the_athlete_s_time_caps_the_peak_and_says_so():
    tight = _balanced(dataclasses.replace(INPUTS, minutes_per_week=240))
    assert max(week.volume_km for week in tight.weeks) <= 240 * 60 / 360 + ROUNDING_KM
    assert TrameRisk.TIME_BOUND in {risk.kind for risk in tight.risks}


def test_a_volume_held_under_the_template_s_domain_is_a_risk():
    low = _balanced(dataclasses.replace(INPUTS, start_km=20, held_km=25))
    assert TrameRisk.BELOW_DOMAIN in {risk.kind for risk in low.risks}


def test_the_specific_block_carries_growing_segments_at_goal_pace():
    trame = _balanced()
    segments = [week.segment.km for week in trame.weeks
                if week.phase == PlanPhase.SPECIFIC and week.segment]
    assert segments and segments == sorted(segments)


def test_a_general_block_holds_its_level_s_band():
    maintain = generator.general(TEMPLATES.general_at(GeneralLevel.MAINTAIN), INPUTS)
    assert maintain.kind == CycleKind.GENERAL and len(maintain.weeks) == 4
    assert all(35 <= week.volume_km <= 40 for week in maintain.weeks), "70-80 % of 50 km"
    assert len(maintain.keys) == 4, "one key session a week"
    progress = generator.general(TEMPLATES.general_at(GeneralLevel.PROGRESS), INPUTS)
    volumes = [week.volume_km for week in progress.weeks]
    assert volumes == sorted(volumes) and volumes[-1] <= 50 + ROUNDING_KM
    assert generator.general(TEMPLATES.general_at(GeneralLevel.LIGHT), INPUTS).keys == ()


def test_a_small_volume_held_is_its_own_general_band():
    small = generator.general(TEMPLATES.general_at(GeneralLevel.MAINTAIN),
                              dataclasses.replace(INPUTS, start_km=30, held_km=30))
    assert all(week.volume_km == 30 for week in small.weeks)


def test_a_recovery_comes_back_within_its_band_without_a_quality_session():
    trame = generator.recovery(TEMPLATES.recovery_after(42.2), INPUTS)
    assert trame.kind == CycleKind.RECOVERY and trame.keys == ()
    assert [week.volume_km for week in trame.weeks] == [15.0, 25.0], "30 then 50 % of 50 km"


def test_a_rebuild_climbs_from_its_floor_to_the_volume_held():
    trame = generator.rebuild(TEMPLATES.rebuild, dataclasses.replace(INPUTS, start_km=5))
    volumes = [week.volume_km for week in trame.weeks]
    assert volumes[0] == 25.0 and volumes[-1] == 50.0
    for before, week in itertools.pairwise(volumes):
        assert week <= before * 1.1 + ROUNDING_KM


def test_no_week_carries_more_quality_sessions_than_rule_n1_allows():
    template = TEMPLATES.preparation_for(21.1, RaceTerrain.ROAD)
    goal = Goal(dt.date(2030, 11, 1), 21.1)
    free = generator.candidates(template, goal, INPUTS)[2]
    assert max(week.qualities for week in free.weeks) == 2
    capped = generator.candidates(template, goal, dataclasses.replace(INPUTS, qualities_max=1))[2]
    assert max(week.qualities for week in capped.weeks) == 1
    assert all(len([key for key in capped.keys if key.week == week.label and key.role != "test"])
               <= 1 for week in capped.weeks)
    general = generator.general(TEMPLATES.general_at(GeneralLevel.MAINTAIN),
                                dataclasses.replace(INPUTS, qualities_max=0))
    assert not general.keys and all(week.qualities == 0 for week in general.weeks)
