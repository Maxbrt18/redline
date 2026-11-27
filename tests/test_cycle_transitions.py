"""The cycle that follows the active one: always one in force, never a preparation entered
without the athlete."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from redline.model import cycle_templates, cycle_transitions, iso_week
from redline.model import plan_generator as generator
from redline.model import race as race_model
from redline.model.cycle_transitions import Standing, Transition
from redline.model.cycles import CycleVersion
from redline.model.efforts import Break
from redline.model.errors import ConfigError
from redline.model.vocabulary import (
    CycleCause,
    CycleKind,
    CycleVersionState,
    GeneralLevel,
    RaceTerrain,
)

TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)
#: A Wednesday.
TODAY = dt.date(2030, 11, 27)
NEXT = iso_week.shift(iso_week.of(TODAY), 1)
STANDING = Standing(run_km=40, start_km=40, floor_km=25)
INPUTS = generator.Inputs(NEXT, 40, 14, 50, 10, 10)


def race(key: str, on: dt.date, state: str, distance_km: float | None = 10,
         priority: str = "a") -> race_model.Race:
    context = {} if distance_km is None else {"distance_km": distance_km}
    return race_model.from_row({"deadline_key": key, "kind": "race", "priority": priority,
                                "race_state": state, "status": "scheduled", "due_on": on,
                                "context": context})


def version(kind: CycleKind, *, origin: str = NEXT, race_key: str | None = None,
            level: GeneralLevel | None = None, created_on: dt.date = TODAY) -> CycleVersion:
    inputs = generator.Inputs(origin, 40, 14, 50, 10, 10)
    if kind == CycleKind.GENERAL:
        trame = generator.general(TEMPLATES.general_at(level or GeneralLevel.MAINTAIN), inputs)
    elif kind == CycleKind.RECOVERY:
        trame = generator.recovery(TEMPLATES.recovery_after(10), inputs)
    elif kind == CycleKind.REBUILD:
        trame = generator.rebuild(TEMPLATES.rebuild, inputs)
    else:
        template = TEMPLATES.preparation_for(21.1, RaceTerrain.ROAD)
        trame = generator.candidates(template, generator.Goal(TODAY + dt.timedelta(weeks=12),
                                                              21.1), inputs)[0]
    return CycleVersion(uuid.uuid4(), uuid.uuid4(), 1, CycleVersionState.ACTIVE, trame,
                        CycleCause.TRANSITION, race_key, level, created_on)


def following(active, races=(), breaks=(), standing=STANDING, today=TODAY, first=False):
    return cycle_transitions.next_cycle(active, races, breaks, standing, today, TEMPLATES,
                                        first=first)


def _last_day_of(found: CycleVersion) -> dt.date:
    """The Sunday of its last week, when its review is written."""
    return iso_week.monday(found.last_week) + dt.timedelta(days=6)


def test_without_a_cycle_the_general_preparation_starts_and_the_first_is_the_takeover():
    assert following(None, first=True) == Transition(CycleKind.GENERAL, CycleCause.MIGRATION,
                                                     level=GeneralLevel.MAINTAIN)
    assert following(None).cause == CycleCause.TRANSITION


def test_without_a_cycle_a_race_run_within_its_recovery_starts_one():
    ran = race("ten", TODAY - dt.timedelta(days=3), "run")
    assert following(None, [ran]) == Transition(CycleKind.RECOVERY, CycleCause.TRANSITION,
                                                race=ran)
    long_ago = race("ten", TODAY - dt.timedelta(weeks=3), "run")
    assert following(None, [long_ago]).kind == CycleKind.GENERAL, "its recovery is over"
    unknown = race("ten", TODAY - dt.timedelta(days=3), "run", distance_km=None)
    assert following(None, [unknown]).kind == CycleKind.GENERAL, \
        "a race without its distance has no recovery template"


def test_a_preparation_gives_way_to_a_recovery_once_its_race_is_run():
    preparation = version(CycleKind.PREPARATION, race_key="half")
    ahead = race("half", TODAY + dt.timedelta(weeks=12), "active", 21.1)
    assert following(preparation, [ahead]) is None
    ran = race("half", TODAY - dt.timedelta(days=1), "abandoned", 21.1)
    assert following(preparation, [ran]) == Transition(CycleKind.RECOVERY,
                                                       CycleCause.TRANSITION, race=ran)
    cancelled = race("half", TODAY + dt.timedelta(weeks=12), "cancelled", 21.1)
    assert following(preparation, [cancelled]).kind == CycleKind.GENERAL
    postponed = race("half", TODAY + dt.timedelta(weeks=12), "postponed", 21.1)
    assert following(preparation, [postponed]) is None, "a postponed race is replanned"


def test_the_general_preparation_starts_its_next_block_in_its_last_week():
    general = version(CycleKind.GENERAL, origin=iso_week.of(TODAY), level=GeneralLevel.LIGHT)
    assert following(general) is None
    last = _last_day_of(general)
    assert following(general, today=last - dt.timedelta(days=1)) is None, \
        "the last week is lived within its block"
    assert following(general, today=last) == Transition(
        CycleKind.GENERAL, CycleCause.BLOCK_END, level=GeneralLevel.LIGHT)


def test_a_race_run_during_the_general_preparation_opens_a_recovery():
    general = version(CycleKind.GENERAL, created_on=TODAY - dt.timedelta(days=10))
    ran = race("trail", TODAY - dt.timedelta(days=2), "run", 30, priority="b")
    assert following(general, [ran]).kind == CycleKind.RECOVERY
    before = race("trail", TODAY - dt.timedelta(days=12), "run", 30, priority="b")
    assert following(general, [before]) is None, "a race run before the block was written"


def test_a_recovery_and_a_rebuild_give_way_to_the_general_preparation():
    recovery = version(CycleKind.RECOVERY, origin=iso_week.of(TODAY))
    assert following(recovery, today=_last_day_of(recovery)).kind == CycleKind.GENERAL
    assert following(recovery, standing=Standing(15, 15, 25)) is None, \
        "the weeks around a race are low by design"
    rebuild = version(CycleKind.REBUILD, origin=iso_week.of(TODAY))
    assert following(rebuild, standing=Standing(30, None, 25)) is None
    assert following(rebuild, standing=Standing(50, None, 25)).kind == CycleKind.GENERAL, \
        "the volume held is run again"


def test_a_break_or_a_start_under_the_floor_calls_for_a_rebuild():
    general = version(CycleKind.GENERAL)
    pause = Break("calf", TODAY - dt.timedelta(days=2), TODAY + dt.timedelta(days=5))
    assert following(general, breaks=[pause]) == Transition(CycleKind.REBUILD,
                                                            CycleCause.TRANSITION)
    assert following(general, standing=Standing(20, 20, 25)).kind == CycleKind.REBUILD
    assert following(general, standing=Standing(20, None, 25)) is None, \
        "weeks a cycle planned low are not a drop"
    rebuild = version(CycleKind.REBUILD, origin=iso_week.of(TODAY))
    assert following(rebuild, breaks=[pause], standing=Standing(50, None, 25)) is None, \
        "a rebuild is not started again, nor left during the break"


def test_each_transition_has_its_trame_and_a_preparation_is_never_entered_by_one():
    assert cycle_transitions.trame(Transition(CycleKind.GENERAL, CycleCause.TRANSITION),
                                   TEMPLATES, INPUTS).template == GeneralLevel.MAINTAIN
    ran = race("ten", TODAY, "run")
    recovery = cycle_transitions.trame(Transition(CycleKind.RECOVERY, CycleCause.TRANSITION,
                                                  race=ran), TEMPLATES, INPUTS)
    assert recovery.kind == CycleKind.RECOVERY and recovery.weeks[0].label == NEXT
    assert cycle_transitions.trame(Transition(CycleKind.REBUILD, CycleCause.TRANSITION),
                                   TEMPLATES, INPUTS).kind == CycleKind.REBUILD
    with pytest.raises(ConfigError, match="recovery template"):
        cycle_transitions.trame(Transition(CycleKind.RECOVERY, CycleCause.TRANSITION),
                                TEMPLATES, INPUTS)
    with pytest.raises(ValueError, match="never entered by a transition"):
        cycle_transitions.trame(Transition(CycleKind.PREPARATION, CycleCause.CREATE),
                                TEMPLATES, INPUTS)
