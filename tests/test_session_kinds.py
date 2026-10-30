"""The session-kind registry: one slug per kind, every set derived, every kind labelled."""

from __future__ import annotations

from redline.coach.fr import session_labels
from redline.model import session_kinds


def test_every_kind_has_a_short_label_and_a_name():
    assert set(session_labels.LABELS) == set(session_kinds.BY_SLUG)
    assert all(short and name for short, name in session_labels.LABELS.values())


def test_slugs_are_lowercase_ascii():
    assert all(slug.isascii() and slug == slug.lower() for slug in session_kinds.BY_SLUG)


def test_the_derived_sets_keep_their_meaning():
    assert {"easy", "long", "threshold", "hills", "strides", "cadence",
            "race"} == session_kinds.RUN
    assert {"easy", "long"} == session_kinds.EASY_RUNS
    assert {"threshold", "hills"} == session_kinds.QUALITY
    assert {"threshold", "hills", "strides"} == session_kinds.REPETITIONS
    assert {"long", "threshold", "hills", "race"} == session_kinds.DEMANDING
    assert {"hike", "bike", "strength"} == session_kinds.HALF_LOAD
    assert set(session_kinds.READINGS) == {"wake-trace", "rest-reading"}
    assert "strength" in session_kinds.EFFORT and "instruments" not in session_kinds.EFFORT


def test_a_strava_sport_matches_the_planned_kinds_of_its_family():
    assert session_kinds.planned_for_sport("TrailRun") == (
        "easy", "long", "threshold", "hills", "strides", "cadence", "race")
    assert session_kinds.planned_for_sport("Walk") == ("hike",)
    assert session_kinds.planned_for_sport("WeightTraining") == ()


def test_the_page_receives_every_kind_with_its_family():
    page = session_labels.for_page()
    assert page["long"] == {"short": "SL", "name": "Sortie longue", "family": "run"}
    assert set(page) == set(session_kinds.BY_SLUG)
