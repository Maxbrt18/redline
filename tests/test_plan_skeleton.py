"""A plan skeleton, built backwards from the race by the engine."""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from redline.model import plan_skeleton, plan_templates
from redline.model.plan_skeleton import Levers, Limits, SkeletonRisk, Start
from redline.model.race import Goal
from redline.model.vocabulary import ClimbIntent, PlanPhase, RaceTerrain

TEMPLATES = plan_templates.load()
HALF = plan_templates.for_race(TEMPLATES, 21.1, RaceTerrain.ROAD)
TRAIL = plan_templates.for_race(TEMPLATES, 30, RaceTerrain.TRAIL)
GOAL = Goal(21.1, RaceTerrain.ROAD, peak_volume_km=50, peak_long_run_km=20, quality_per_week=2)
START = Start(30, 14)
LIMITS = Limits(10, 10, 50, 2)
FIRST = dt.date(2030, 11, 25)
RACE = dt.date(2031, 4, 6)
BALANCED = Levers(6, 4, PlanPhase.SPECIFIC)


def build(levers=BALANCED, goal=GOAL, start=START, limits=LIMITS, first=FIRST, race=RACE,
          template=HALF, **kwargs):
    return plan_skeleton.generate(template, goal, levers, start, limits, first, race, **kwargs)


def phases(skeleton):
    return [week.phase for week in skeleton.weeks]


def test_the_weeks_run_from_this_week_to_the_race_week_taper_last():
    skeleton = build()
    assert skeleton.weeks[0].label == "2030-W48" and skeleton.weeks[-1].label == "2031-W14"
    assert phases(skeleton)[-2:] == [PlanPhase.TAPER, PlanPhase.TAPER]
    assert phases(skeleton).count(PlanPhase.SPECIFIC) == 6, "the lever sets the specific block"
    assert phases(skeleton).count(PlanPhase.DEVELOPMENT) == 6
    assert phases(skeleton).count(PlanPhase.BASE) == 5, "base absorbs what remains"
    assert skeleton.risks == ()


def test_short_of_weeks_development_then_specific_shrink_before_base_falls_short():
    tight = build(first=dt.date(2030, 12, 16))
    assert len(tight.weeks) == 16
    assert [phases(tight).count(phase) for phase in (
        PlanPhase.BASE, PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC)] == [4, 5, 5]
    assert [risk.kind for risk in tight.risks] == [SkeletonRisk.SPECIFIC_SHORT]
    shortest = build(first=dt.date(2031, 2, 2))
    assert {risk.kind for risk in shortest.risks} >= {SkeletonRisk.BASE_SHORT,
                                                      SkeletonRisk.SPECIFIC_SHORT}
    assert phases(shortest)[-2:] == [PlanPhase.TAPER, PlanPhase.TAPER], "the taper stays"


def test_a_deload_precedes_the_specific_block_and_never_the_taper():
    skeleton = build()
    labels = [week.label for week in skeleton.weeks]
    specific = phases(skeleton).index(PlanPhase.SPECIFIC)
    deloads = [index for index, week in enumerate(skeleton.weeks) if week.deload]
    assert specific - 1 in deloads
    assert all(later - earlier == 4 for earlier, later in itertools.pairwise(deloads))
    taper = phases(skeleton).index(PlanPhase.TAPER)
    assert taper - 1 not in deloads, "the week before the taper is the last key week"
    assimilated = build(limits=Limits(10, 10, 50, 2, frozenset({labels[2]})))
    assert assimilated.weeks[2].deload, "the rulebook's assimilation weeks are deloads too"


def test_volume_and_long_run_climb_to_the_peak_the_week_before_the_taper():
    skeleton = build()
    taper = phases(skeleton).index(PlanPhase.TAPER)
    peak = skeleton.weeks[taper - 1]
    assert (peak.volume_km, peak.long_run_km) == (50, 20)
    deload = next(week for week in skeleton.weeks if week.deload)
    before = skeleton.weeks[skeleton.weeks.index(deload) - 1]
    assert deload.volume_km == pytest.approx(before.volume_km * 0.75, abs=0.1)
    assert skeleton.weeks[taper].volume_km == 25 and skeleton.weeks[-1].long_run_km == 0


def test_lost_weeks_are_never_made_up_the_peak_is_out_of_reach_instead():
    skeleton = build(start=Start(20, 8), first=dt.date(2031, 1, 26))
    builds = [week for week in skeleton.weeks if not week.deload
              and week.phase in (PlanPhase.BASE, PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC)]
    assert all(later.volume_km <= earlier.volume_km * 1.1 + 0.1
               for earlier, later in itertools.pairwise(builds))
    assert {risk.kind for risk in skeleton.risks} >= {SkeletonRisk.PEAK_VOLUME_UNREACHED,
                                                      SkeletonRisk.PEAK_LONG_RUN_UNREACHED}


def test_the_second_quality_arrives_with_its_lever_and_a_deload_adds_none():
    late = build()
    assert {week.qualities for week in late.weeks
            if week.phase == PlanPhase.DEVELOPMENT} == {1}
    early = build(Levers(6, 4, PlanPhase.DEVELOPMENT))
    first_development = next(week for week in early.weeks
                             if week.phase == PlanPhase.DEVELOPMENT and not week.deload)
    assert first_development.qualities == 2
    capped = build(limits=Limits(10, 10, 50, 1))
    assert max(week.qualities for week in capped.weeks) == 1, "rule n1 caps every week"


def test_the_families_are_the_templates_the_athlete_may_run():
    goal = Goal(21.1, RaceTerrain.ROAD, peak_volume_km=50, peak_long_run_km=20,
                families=("threshold",))
    skeleton = build(goal=goal)
    assert {family for week in skeleton.weeks for family in week.families} == {"threshold"}
    none = build(goal=Goal(21.1, RaceTerrain.ROAD, peak_volume_km=50, peak_long_run_km=20,
                           families=()))
    assert {week.qualities for week in none.weeks} == {0}


def test_the_climb_intent_defaults_from_the_terrain():
    assert {week.climb_intent for week in build().weeks} == {ClimbIntent.RUN_SLOW}
    trail = Goal(30, RaceTerrain.TRAIL, peak_volume_km=55, peak_long_run_km=28,
                 power_hike_from_pct=12)
    weeks = build(goal=trail, template=TRAIL).weeks
    assert {(week.climb_intent, week.power_hike_from_pct) for week in weeks} == {
        (ClimbIntent.POWER_HIKE, 12)}


def test_a_recent_race_opens_with_a_recovery():
    skeleton = build(recovering=True)
    assert skeleton.weeks[0].phase == PlanPhase.RECOVERY
    assert skeleton.weeks[0].qualities == 0
    assert skeleton.weeks[0].climb_intent == ClimbIntent.EASY


def test_the_candidates_vary_the_real_levers():
    found = plan_skeleton.candidates(HALF, GOAL, START, LIMITS, FIRST, RACE)
    assert [skeleton.candidate for skeleton in found] == ["cautious", "balanced", "specific"]
    assert len({skeleton.levers for skeleton in found}) == 3
    assert found[0].levers == Levers(5, 3, PlanPhase.SPECIFIC)
    assert found[2].levers == Levers(6, 4, PlanPhase.DEVELOPMENT)


def test_a_goal_without_its_peaks_has_no_skeleton():
    with pytest.raises(ValueError, match="peak"):
        build(goal=Goal(21.1, RaceTerrain.ROAD))


def test_a_week_that_adds_a_quality_holds_the_volume_of_the_last_build_week():
    skeleton = build()
    builds = [week for week in skeleton.weeks if not week.deload
              and week.phase in plan_skeleton.BUILD_PHASES]
    added = [(before, week) for before, week in itertools.pairwise(builds)
             if week.qualities > before.qualities]
    assert added, "the second quality arrives in this skeleton"
    for before, week in added:
        assert (week.volume_km, week.long_run_km) == (before.volume_km, before.long_run_km)
    rising = [(before, week) for before, week in itertools.pairwise(builds)
              if week.qualities == before.qualities and week.volume_km > before.volume_km]
    assert rising, "the ramp resumes the week after"


def test_each_week_expects_its_load_from_what_a_quality_costs_the_athlete():
    held = plan_skeleton.Held(quality_premium=4.0)
    skeleton = build(held=held)
    week = next(week for week in skeleton.weeks if week.qualities == 2)
    assert week.load == pytest.approx(week.volume_km + 8.0, abs=0.05)
    unknown = build()
    assert all(week.load is None for week in unknown.weeks if week.qualities)
    easy_only = build(goal=Goal(21.1, RaceTerrain.ROAD, peak_volume_km=50, peak_long_run_km=20,
                                families=()))
    assert all(week.load == week.volume_km for week in easy_only.weeks), \
        "a week without quality counts its kilometres"


def test_the_counter_reading_holds_a_candidate_to_what_the_athlete_held():
    skeleton = build(held=plan_skeleton.Held(quality_premium=4.0, week_load=45.0,
                                             long_share=0.35))
    load = next(risk for risk in skeleton.risks if risk.kind == SkeletonRisk.LOAD_ABOVE_HELD)
    peak = max(skeleton.weeks, key=lambda week: week.load or 0.0)
    assert (load.value, load.reference, load.week) == (peak.load, 45.0, peak.label)
    share = next(risk for risk in skeleton.risks
                 if risk.kind == SkeletonRisk.LONG_SHARE_ABOVE_HELD)
    assert share.value > 35 and share.reference == 35
    roomy = build(held=plan_skeleton.Held(quality_premium=4.0, week_load=80.0, long_share=0.6))
    assert roomy.risks == (), "within what was held, nothing to read"
