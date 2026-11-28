"""The plan over a whole preparation: what an athlete who takes a candidate and follows the
review week by week is shown, and what the gate lets them run (`trajectory.py`).

The trame is generated once, when the candidate is taken, and read by every review after
it: what the recomputed skeleton lost week after week (its deloads, its ramp, its phases)
holds, and the gate holds each week to it: a taper week to the week before the taper, its
cut to the taper band. The strict expected failure is what is still missing, labelled with
its code in the planning audit (C3).
"""

from __future__ import annotations

import itertools

import pytest
import trajectory
from trajectory import Lived

from redline.model import iso_week
from redline.model.plan_generator import BUILD_PHASES
from redline.model.vocabulary import Decider, HypothesisKind, HypothesisOutcome, PlanPhase
from redline.service import plan_revision

#: The order a preparation's phases come in.
ORDER = (PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC, PlanPhase.TAPER)
#: A progressive taper's first week: well inside the band, which bounds the mean drop.
FIRST_TAPER_CUT = 0.2
#: A trame's figures are rounded to 0.1 km.
ROUNDING_KM = 0.1


@pytest.fixture(scope="module")
def followed() -> tuple[Lived, ...]:
    """Every week of the half followed from 30 km a week, nothing missed."""
    return trajectory.simulate().weeks


def _builds(lived: tuple[Lived, ...]) -> list[Lived]:
    return [week for week in lived if week.shown and week.shown.phase in BUILD_PHASES]


def test_the_review_shows_a_bound_for_every_week_to_the_race(followed):
    assert [lived.week for lived in followed][-1] == iso_week.of(trajectory.RACE_ON)
    assert all(lived.shown is not None for lived in followed)
    ranks = [ORDER.index(lived.shown.phase) for lived in followed if lived.shown]
    assert ranks == sorted(ranks)


def test_the_gate_accepts_what_the_review_shows_an_athlete_who_follows(followed):
    refused = [(lived.week, lived.shown.volume_km, lived.ceiling_km) for lived in followed
               if lived.shown and lived.shown.volume_km > lived.ceiling_km + ROUNDING_KM]
    assert refused == []


def test_the_review_shows_every_deload_the_trame_placed_and_the_athlete_runs_it(followed):
    store = trajectory.simulate().store
    assert store.cycle is not None
    placed = [week.label for week in store.cycle.trame.weeks if week.deload]
    assert placed, "a preparation of this length has a deload"
    for before, week in itertools.pairwise(followed):
        if week.week in placed:
            assert week.shown and before.shown and week.shown.volume_km < before.shown.volume_km
            assert week.run_km < before.run_km, week.week


def test_an_athlete_who_follows_reaches_the_trame_s_peak_before_the_taper(followed):
    peak = max(lived.shown.volume_km for lived in _builds(followed) if lived.shown)
    assert max(lived.run_km for lived in followed) >= peak - ROUNDING_KM


def test_an_athlete_who_follows_lives_every_phase_of_the_template(followed):
    lived = {week.shown.phase for week in followed if week.shown}
    assert lived >= {PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC, PlanPhase.TAPER}, lived


def test_after_missed_weeks_the_review_still_gives_a_volume_to_run():
    lived = trajectory.simulate(missed={3, 4, 5}).weeks
    after = lived[6:]
    assert all(week.shown and week.shown.volume_km > 0 for week in after)


def test_after_a_missed_week_the_athlete_climbs_back_to_what_they_held():
    lived = trajectory.simulate(missed={3}).weeks
    before = lived[2].run_km
    builds = _builds(lived[4:])
    assert max(week.run_km for week in builds) >= before, [week.run_km for week in builds]


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
    assert trajectory.CyclePlanner().refusals(store, week, cut) == []


# ------------------------------------------------------------------ what is still missing

#: A trame multiplies the start, and zero times anything is zero.
C3 = "C3: from a zero start every volume of the trame is zero"


def test_the_review_shows_the_bound_the_gate_applies_before_the_taper(followed):
    apart = [(lived.week, lived.shown.volume_km, lived.ceiling_km) for lived in followed
             if lived.shown and lived.shown.phase != PlanPhase.TAPER
             and abs(lived.shown.volume_km - lived.ceiling_km) > ROUNDING_KM]
    assert apart == []


@pytest.mark.xfail(strict=True, reason=C3)
def test_an_athlete_without_history_is_given_a_volume_to_run():
    lived = trajectory.simulate(start_km=0, start_long_km=0).weeks
    assert all(week.shown and week.shown.volume_km > 0 for week in lived), \
        [week.shown for week in lived]


def _replanned(lived: tuple[Lived, ...]) -> list[str]:
    return [week.week for week in lived if week.review.outcome == HypothesisOutcome.REPLAN]


def test_an_athlete_who_follows_never_drops_a_hypothesis(followed):
    assert [week.review.fallen for week in followed if week.review.fallen] == []


def test_one_missed_week_never_replans_the_preparation():
    for missed in range(3, 6):
        assert _replanned(trajectory.simulate(missed={missed}).weeks) == [], missed


def test_two_weeks_run_far_under_the_trame_replan_it_on_the_second_sunday():
    lived = trajectory.simulate(short={2: 0.6, 3: 0.6}).weeks
    replanned = _replanned(lived)
    assert replanned == [lived[5].week], "once, at the first review after the second short week closed"
    found = lived[5].review.fallen
    assert [item.kind for item in found] == [HypothesisKind.VOLUME_HELD]


def test_two_weeks_run_a_little_under_the_trame_hold_it():
    assert _replanned(trajectory.simulate(short={2: 0.85, 3: 0.85}).weeks) == []


def test_a_first_time_not_run_slides_and_replans_only_once_no_week_can_hold_it():
    lived = trajectory.simulate(skip_firsts=True).weeks
    replanned = _replanned(lived)
    assert len(replanned) == 1, "the review asks once, when the last build week is gone"
    [week] = [item for item in lived if item.week == replanned[0]]
    assert [found.kind for found in week.review.fallen] == [HypothesisKind.FIRSTS]
    builds = [item.week for item in lived if item.shown and item.shown.phase in BUILD_PHASES]
    assert replanned[0] > builds[-1], "while a build week is left, the first slides"


def test_an_athlete_who_runs_the_first_time_never_drops_it(followed):
    assert not [item for item in followed
                if HypothesisKind.FIRSTS in {found.kind for found in item.review.fallen}]
