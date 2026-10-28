"""The session-kind registry: one slug per kind, every set derived, every kind labelled."""

from __future__ import annotations

import importlib.util

import sqlalchemy as sa
from conftest import ENGINE_ROOT

from scripts.coach.fr import session_labels
from scripts.model import session_kinds
from scripts.storage import schema

MIGRATION = ENGINE_ROOT / "migrations" / "versions" / "20261028_0004_session_kinds_in_english.py"


def _migration():
    spec = importlib.util.spec_from_file_location("session_kinds_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_the_database_constraints_list_the_registry():
    """The migration freezes the lists; schema.py derives them. They must agree."""
    migration = _migration()
    rules = {constraint.name: str(constraint.sqltext)
             for table in schema.metadata.tables.values()
             for constraint in table.constraints if isinstance(constraint, sa.CheckConstraint)}
    for name, (_, rule) in migration.NEW.items():
        assert rules[name] == rule, name


def test_every_old_kind_has_a_new_one():
    migration = _migration()
    assert {new for new, _ in migration.OBSERVATION_KINDS.values()} == set(
        session_kinds.RECORDED)
    assert {new for new, _ in migration.PLANNED_WORKOUTS.values()} == set(
        session_kinds.PLANNED)
