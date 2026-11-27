"""A trame written to its three tables and read back is the same trame."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from redline.model import cycle_templates, cycles, iso_week
from redline.model import plan_generator as generator
from redline.model.progression import Dose
from redline.model.vocabulary import CycleCause, CycleVersionState, GeneralLevel, RaceTerrain

TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)
INPUTS = generator.Inputs("2030-W49", 42.3, 15.2, 55.0, 10, 10, minutes_per_week=300,
                          easy_pace_s=331.5, calibrated=False,
                          calibration_on=dt.date(2030, 9, 1),
                          lineages=(("threshold", Dose.of(reps=4, rep_seconds=540,
                                                          recovery_seconds=60)),))


def _trames():
    template = TEMPLATES.preparation_for(42.2, RaceTerrain.ROAD)
    goal = generator.Goal(dt.date(2031, 4, 13), 42.2)
    return {
        "preparation": generator.candidates(template, goal, INPUTS)[1],
        "general": generator.general(TEMPLATES.general_at(GeneralLevel.PROGRESS), INPUTS),
        "recovery": generator.recovery(TEMPLATES.recovery_after(42.2), INPUTS),
        "rebuild": generator.rebuild(TEMPLATES.rebuild, INPUTS),
    }


def _stored(rows):
    """What JSONB and NUMERIC give back: JSON values, numbers as they were sent."""
    return json.loads(json.dumps(rows, default=str))


@pytest.mark.parametrize("kind", ["preparation", "general", "recovery", "rebuild"])
def test_a_trame_read_back_from_its_rows_is_the_same_trame(kind):
    trame = _trames()[kind]
    values = cycles.version_values(trame)
    row = {**{key: _stored(value) if key in ("levers", "goal", "hypotheses", "risks") else value
              for key, value in values.items()},
           "id": uuid.uuid4(), "cycle_key": uuid.uuid4(), "version": 1, "state": "active",
           "cause": {"kind": "transition"}, "race_key": None, "level": None,
           "created_at": dt.datetime(2030, 11, 27, 20, tzinfo=dt.UTC)}
    found = cycles.from_rows(row, _stored(cycles.week_values(trame)),
                             _stored(cycles.key_values(trame)))
    assert found.trame == trame
    assert (found.state, found.cause, found.created_on) == (
        CycleVersionState.ACTIVE, CycleCause.TRANSITION, dt.date(2030, 11, 27))
    assert found.last_week == trame.weeks[-1].label


def test_a_version_spans_whole_weeks():
    trame = _trames()["general"]
    values = cycles.version_values(trame)
    assert values["starts_on"] == iso_week.monday("2030-W49")
    assert values["ends_on"].isoweekday() == 7
    assert values["hypotheses"]["calibration_on"] == "2030-09-01"
    assert values["hypotheses"]["lineages"] == [
        ["threshold", [["reps", 4], ["rep_seconds", 540], ["recovery_seconds", 60]]]]
