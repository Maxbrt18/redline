"""`redline cycle candidates`: the trames generated from the athlete's real state."""

from __future__ import annotations

import datetime as dt

import pytest
import trajectory
from conftest import AGENT_CLI_DIR

from redline import cli
from redline.analysis import cycle_inputs, progression_runs
from redline.cli import cycle
from redline.model import cycle_templates, iso_week
from redline.model.errors import InputError
from redline.model.progression import Dose
from redline.model.vocabulary import CycleKind, GeneralLevel, RaceState

TODAY = iso_week.monday(trajectory.FIRST_WEEK) - dt.timedelta(days=1)
TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)


class Store(trajectory.Store):
    def __init__(self, *others, habits=(), calibrations=()):
        super().__init__(trajectory.HALF, trajectory.RACE_ON, others)
        self.habit_rows, self.calibrations = list(habits), list(calibrations)

    def habits(self, on=None, until=None):
        return self.habit_rows

    def calibration_rows(self):
        return self.calibrations


def store(*others, weeks=((30, 14), (34, 15), (28, 12), (32, 14)), **kwargs):
    found = Store(*others, **kwargs)
    for back, (km, long_km) in enumerate(reversed(weeks), start=1):
        trajectory.run_week(found, iso_week.shift(trajectory.FIRST_WEEK, -back), km, long_km)
    return found


@pytest.fixture(autouse=True)
def no_lineages(monkeypatch):
    monkeypatch.setattr(progression_runs, "proposals", lambda store, book, today: [])


def _habit(weekday, kind="as_planned", minutes=None, run_too=None):
    return {"id": f"h{weekday}", "weekday": weekday, "starts_on": dt.date(2030, 1, 1),
            "ends_on": None, "kind": kind, "minutes": minutes, "run_too": run_too}


def test_the_inputs_read_the_closed_weeks_and_the_volume_held():
    found = cycle_inputs.inputs(store(), TEMPLATES, TODAY)
    assert found.origin == trajectory.FIRST_WEEK, "a cycle starts the week after today"
    assert found.start_km == pytest.approx((30 + 34 + 28) / 3, abs=0.1), \
        "today's week is not closed yet"
    assert found.held_km == 34 and found.start_long_km == 15
    assert found.easy_pace_s == trajectory.PACE_S
    assert found.calibrated is False and found.minutes_per_week is None


def test_a_start_under_the_floor_is_raised_to_it():
    found = cycle_inputs.inputs(store(weeks=((60, 20), (10, 5), (10, 5), (10, 5))), TEMPLATES,
                                TODAY)
    assert found.start_km == 30, "half of the 60 km held"


def test_the_habits_give_the_minutes_left_for_running_when_every_day_states_them():
    week = [_habit(1, "off"), _habit(2, minutes=45), _habit(3, "other_sport"),
            _habit(4, minutes=60), _habit(5, "off"), _habit(6, "easy", minutes=50),
            _habit(7, minutes=90)]
    assert cycle_inputs.inputs(store(habits=week), TEMPLATES, TODAY).minutes_per_week == 245
    open_day = [*week[:6], _habit(7)]
    assert cycle_inputs.inputs(store(habits=open_day), TEMPLATES, TODAY).minutes_per_week is None


def test_a_lineage_gives_its_dose_to_the_workout_it_follows(monkeypatch):
    class Proposal:
        lineage, ceiling, dose = "threshold-reps", Dose.of(reps=4, rep_seconds=540), None

    class Lineage:
        workout = "threshold"

    class Progression:
        def of(self, slug):
            return Lineage() if slug == "threshold-reps" else None

    class Book:
        parameters = cycle_inputs.rulebook.current().parameters
        progression = Progression()

    monkeypatch.setattr(cycle_inputs.rulebook, "current", lambda: Book())
    monkeypatch.setattr(progression_runs, "proposals", lambda store, book, today: [Proposal()])
    found = cycle_inputs.inputs(store(), TEMPLATES, TODAY)
    assert found.lineage("threshold") == Dose.of(reps=4, rep_seconds=540)


def test_the_goal_s_candidates_come_by_default_with_their_risks_and_weeks():
    trames, refs = cycle.candidates(store(), TODAY, None, None)
    assert [trame.candidate for trame in trames] == ["cautious", "balanced", "specific"]
    lines = cycle.render(trames, refs)
    assert lines[0].startswith("Préparation « half-road » à partir de la semaine")
    assert "  Aucune calibration : les allures attendent un test." in lines
    assert any(line.startswith("Candidat « balanced »") for line in lines)
    assert any("test 1 200 m puis 3 600 m à fond" in line for line in lines)
    assert any("(allure à calibrer)" in line for line in lines)


def test_without_an_active_goal_the_general_preparation_comes_at_the_default_level():
    goalless = store()
    goalless.goal = {**trajectory.HALF}
    trames, _ = cycle.candidates(goalless, trajectory.RACE_ON + dt.timedelta(days=1), None,
                                 None)
    assert [trame.kind for trame in trames] == [CycleKind.GENERAL]
    assert trames[0].template == GeneralLevel.MAINTAIN
    trames, refs = cycle.candidates(store(), TODAY, CycleKind.GENERAL, GeneralLevel.PROGRESS)
    assert trames[0].template == GeneralLevel.PROGRESS
    assert cycle.render(trames, refs)[0].startswith("Préparation générale « progresser »")


def test_a_recovery_follows_the_last_race_run_and_a_rebuild_needs_none():
    with pytest.raises(InputError, match="la récupération suit une course"):
        cycle.candidates(store(), TODAY, CycleKind.RECOVERY, None)
    ran = {"deadline_key": "ten", "kind": "race", "priority": "b",
           "race_state": RaceState.RUN, "status": "done", "due_on": dt.date(2030, 11, 10),
           "item": "Ten", "context": {"distance_km": 10, "terrain": "road"},
           "fact_owner": "athlete/goals.md", "athlete_label": "Ten"}
    trames, _ = cycle.candidates(store(ran), TODAY, CycleKind.RECOVERY, None)
    assert trames[0].template == "recovery-25", "the test vault's first band"
    trames, refs = cycle.candidates(store(), TODAY, CycleKind.REBUILD, None)
    assert cycle.render(trames, refs)[0].startswith("Reprise « rebuild »")


def test_the_command_is_listed_for_the_coach():
    assert "cycle" in cli.COMMANDS
    assert "cycle" in (AGENT_CLI_DIR / "agent-cli-tools.txt").read_text().splitlines()
