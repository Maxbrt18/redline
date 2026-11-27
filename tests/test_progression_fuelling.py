"""Gut training at the gate: a carbohydrate target against the gut-training lineage."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from redline.analysis import progression_fuelling
from redline.model import rulebook
from redline.model.progression import Lineages

TODAY = dt.date(2026, 8, 16)
BOOK = rulebook.current()


class Store:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def fuelled_sessions(self):
        return self.sessions


def fuelled(days_ago, target, gut, taken=None):
    return {"on": TODAY - dt.timedelta(days=days_ago), "session": f"sl-{days_ago}",
            "target_g_h": float(target), "carbs_taken_g_h": float(taken or target),
            "gut_symptoms": None if gut is None else float(gut)}


def revision(*targets, status="planned"):
    return {"revision": 1, "sessions": [
        {"id": f"sl-{index}", "status": status, "carbs_target_g_h": target}
        for index, target in enumerate(targets)]}


def refused(store, *targets, **kwargs):
    return progression_fuelling.ceiling_errors(store, BOOK, "2026-W34", revision(*targets,
                                                                                **kwargs),
                                               TODAY)


@pytest.mark.parametrize(("target", "gut", "proposed", "allowed"), [
    (40, 2, 55, True), (40, 2, 56, False), (40, 3, 55, True), (40, 4, 41, False),
    (40, 4, 40, True), (75, 1, 90, True), (75, 1, 91, False),
])
def test_one_step_at_most_after_a_fuelled_session_the_gut_tolerated(target, gut, proposed,
                                                                     allowed):
    assert (refused(Store(fuelled(7, target, gut)), proposed) == []) is allowed


def test_the_refusal_names_the_session_the_target_and_the_ceiling():
    [refusal] = refused(Store(fuelled(7, 60, 2)), 80)
    assert refusal.startswith("2026-W34 revision 1.sl-0.carbs_target_g_h: 80 g/h is above "
                              "the ceiling of gut-training (carbs_g_h=75")


def test_a_session_awaiting_its_gut_answer_holds_the_last_tolerated_target():
    store = Store(fuelled(3, 55, None), fuelled(10, 40, 2))
    assert refused(store, 40) == []
    [refusal] = refused(store, 55)
    assert "(carbs_g_h=40, await-reading)" in refusal


def test_nothing_is_bounded_before_a_first_fuelled_session_or_without_a_target():
    assert refused(Store(), 90) == []
    assert refused(Store(fuelled(7, 40, 6))) == []
    assert refused(Store(fuelled(7, 40, 6)), 80, status="cancelled") == []


def test_an_instance_without_a_gut_training_lineage_has_no_check():
    others = tuple(lineage for lineage in BOOK.progression.lineages
                   if lineage.family != "fuelling")
    book = dataclasses.replace(BOOK, progression=Lineages(others))
    assert progression_fuelling.ceiling_errors(Store(fuelled(7, 40, 6)), book, "2026-W34",
                                               revision(80), TODAY) == []
