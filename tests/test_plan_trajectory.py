"""The plan over a whole preparation: what the generator promises, and what an athlete who
follows the review week by week is actually allowed to run (`trajectory.py`).
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest
import trajectory
from trajectory import Lived

from redline.model import iso_week, plan_skeleton, plan_templates
from redline.model.plan_skeleton import BUILD_PHASES, Limits, Start
from redline.model.race import Goal
from redline.model.vocabulary import Decider, PlanPhase, RaceTerrain
from redline.service import plan_revision

TEMPLATES = plan_templates.load()
#: The order a preparation's phases come in.
ORDER = (PlanPhase.RECOVERY, PlanPhase.BASE, PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC,
         PlanPhase.TAPER)
#: A goal each default template serves: distance, terrain, peak volume, peak long run.
GOALS = {"short-road": (10, RaceTerrain.ROAD, 40, 15),
         "half-road": (21.1, RaceTerrain.ROAD, 50, 20),
         "marathon-road": (42.2, RaceTerrain.ROAD, 70, 32),
         "trail": (30, RaceTerrain.TRAIL, 60, 25),
         "ultra": (80, RaceTerrain.TRAIL, 80, 40)}
LIMITS = Limits(ramp_max_pct=10, long_step_max_pct=10, taper_cut_pct=50, quality_per_week=2)
RACE = dt.date(2031, 4, 6)
#: A progressive taper's first week: well inside the band, which bounds the mean drop.
FIRST_TAPER_CUT = 0.2
#: A skeleton's figures are rounded to 0.1 km.
ROUNDING_KM = 0.1


def _skeletons():
    for template, start, weeks, recovering in itertools.product(
            TEMPLATES, (Start(20, 10), Start(35, 16)), (10, 16, 24), (False, True)):
        distance, terrain, peak, long_run = GOALS[template.slug]
        goal = Goal(distance, terrain, peak_volume_km=peak, peak_long_run_km=long_run,
                    quality_per_week=2)
        for slug, levers in plan_skeleton.candidate_levers(template).items():
            yield pytest.param((template, goal, levers, start,
                                RACE - dt.timedelta(weeks=weeks), recovering),
                               id=f"{template.slug}-{slug}-{start.volume_km:g}km-{weeks}w"
                                  + ("-recovering" if recovering else ""))


def _generate(template, goal, levers, start, first, recovering):
    return plan_skeleton.generate(template, goal, levers, start, LIMITS, first, RACE,
                                  recovering=recovering)


@pytest.fixture(params=list(_skeletons()))
def inputs(request):
    return request.param


@pytest.fixture
def skeleton(inputs):
    return _generate(*inputs)


def _builds(skeleton):
    return [week for week in skeleton.weeks if week.phase in BUILD_PHASES and not week.deload]


# ------------------------------------------------------------------ what the generator promises


def test_the_phases_come_in_order_and_the_taper_ends_the_preparation(skeleton):
    ranks = [ORDER.index(week.phase) for week in skeleton.weeks]
    assert ranks == sorted(ranks), [week.phase for week in skeleton.weeks]
    assert skeleton.weeks[-1].phase == PlanPhase.TAPER
    assert skeleton.weeks[-1].label == iso_week.of(RACE)


def test_no_build_week_climbs_faster_than_the_ramp(skeleton):
    for before, week in itertools.pairwise(_builds(skeleton)):
        volume = before.volume_km * (1 + LIMITS.ramp_max_pct / 100) + ROUNDING_KM
        long_run = before.long_run_km * (1 + LIMITS.long_step_max_pct / 100) + ROUNDING_KM
        assert week.volume_km <= volume, f"{week.label}: {before.volume_km} -> {week.volume_km}"
        assert week.long_run_km <= long_run, \
            f"{week.label}: long run {before.long_run_km} -> {week.long_run_km}"


def test_volume_and_quality_never_rise_in_the_same_week(skeleton):
    for before, week in itertools.pairwise(_builds(skeleton)):
        if week.qualities > before.qualities:
            assert week.volume_km <= before.volume_km + ROUNDING_KM / 2, week.label
            assert week.long_run_km <= before.long_run_km + ROUNDING_KM / 2, week.label


def test_no_deload_falls_on_the_week_before_the_taper(skeleton):
    taper = next(index for index, week in enumerate(skeleton.weeks)
                 if week.phase == PlanPhase.TAPER)
    assert taper == 0 or not skeleton.weeks[taper - 1].deload


def test_the_same_inputs_give_the_same_skeleton(inputs, skeleton):
    assert _generate(*inputs) == skeleton


# ------------------------------------------------------------------ the preparation as lived


@pytest.fixture(scope="module")
def followed() -> tuple[Lived, ...]:
    """Every week of the half followed from 30 km a week, nothing missed."""
    return trajectory.simulate().weeks


def test_the_review_shows_a_bound_for_every_week_to_the_race(followed):
    assert [lived.week for lived in followed][-1] == iso_week.of(trajectory.RACE_ON)
    assert all(lived.shown is not None for lived in followed)
    ranks = [ORDER.index(lived.shown.phase) for lived in followed if lived.shown]
    assert ranks == sorted(ranks)


def test_the_gate_accepts_what_the_review_shows_an_athlete_who_follows(followed):
    refused = [(lived.week, lived.shown.volume_km, lived.ceiling_km) for lived in followed
               if lived.shown and lived.shown.volume_km > lived.ceiling_km + ROUNDING_KM]
    assert refused == []


def _first_taper_week_cut_by_a_fifth(followed):
    """The store as lived up to the first taper week, and a revision of that week
    running a fifth less than the week before it, decided by judgment."""
    taper = next(index for index, lived in enumerate(followed)
                 if lived.shown and lived.shown.phase == PlanPhase.TAPER)
    week, before = followed[taper].week, followed[taper - 1]
    store = trajectory.simulate(through=before.week).store
    volume = round(before.run_km * (1 - FIRST_TAPER_CUT), 1)
    long_run = round(volume / 3, 1)
    return store, week, trajectory.revision(week, volume, long_run,
                                            trajectory.review_day(week),
                                            decided=Decider.JUDGMENT)


def test_the_taper_band_accepts_a_first_taper_week_cut_by_a_fifth(followed):
    store, week, cut = _first_taper_week_cut_by_a_fifth(followed)
    unread: list[str] = []
    assert plan_revision.taper_errors(store, week, cut, unread.append) == []
    assert not unread
