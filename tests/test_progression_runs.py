"""The run lineages: declared in the rulebook, their dose read from the plan, proposed."""

from __future__ import annotations

import datetime as dt

import pytest
from test_progression_strength import PROGRESSION, book_with
from test_weekly_plan import revision, session

from redline.analysis import expected_rpe, ledger, progression_runs, progression_strength
from redline.analysis.progression import propose
from redline.model import rulebook
from redline.model.progression import Bound, Dose, Move, Signal, VetoKind, default

MONDAY = dt.date(2030, 3, 4)
ZONE = "knee"
RUNS = """reserve_min = 1
following_pain_max = 2

[[progression.lineages]]
slug = "threshold-reps"
family = "intervals"
workout = "threshold"
reps = { minimum = 4, maximum = 6, step = 1 }
rep_seconds = { minimum = 300, maximum = 420, step = 60 }
recovery_seconds = { minimum = 60, maximum = 120, step = 30 }
"""


def day(offset: int) -> dt.date:
    return MONDAY + dt.timedelta(days=offset)


def threshold(on: dt.date, reps: int | None, *, seconds: int | None = 300,
              recovery: int | None = 120) -> dict:
    typed = {name: value for name, value in (("reps", reps), ("rep_seconds", seconds),
                                             ("recovery_seconds", recovery)) if value}
    return session(f"thr-{on.isoformat()}", on.isoformat(), "threshold", **typed)


class Store:
    """Plans by week, kept debrief verdicts by activity, nothing else declared."""

    def __init__(self, plans: dict[str, list[dict]], verdicts: dict[int, list[dict]]):
        self.plans, self.verdicts = plans, verdicts

    def load_plan(self, week):
        sessions = self.plans.get(week)
        return None if sessions is None else {"week": week,
                                              "revisions": [revision(sessions=sessions)]}

    def debrief_verdict(self, activity_id):
        found = self.verdicts.get(activity_id)
        return None if found is None else {"verdict": "conforming", "findings": found}

    def daily_rows(self):
        return []

    def weekly_instrument_rows(self):
        return []


def run(on: dt.date, activity: int, *, rpe=7.0, reserve=1.0, pain=0.5) -> ledger.Row:
    return ledger.Row(on, "threshold", idempotency_key=f"strava:{activity}", km=10, hours=1.0,
                      rpe=rpe, reserve=reserve, pains={ZONE: pain})


def easy(on: dt.date, pain=0.5) -> ledger.Row:
    return ledger.Row(on, "easy", km=8, hours=0.8, pains={ZONE: pain})


def morning(on: dt.date, value=0.0) -> ledger.Row:
    return ledger.Row(on, "wake-trace", pains={ZONE: value})


# ------------------------------------------------------------------ declaration

def test_a_run_lineage_names_its_workout_and_bounds_its_recovery():
    declared = book_with(PROGRESSION + RUNS).progression.of("threshold-reps")
    assert declared.workout == "threshold" and declared.family == "intervals"
    assert declared.bound("recovery_seconds") == Bound(60, 120, 30)
    assert declared.parameter("reserve_min") == 1
    assert declared.parameter("following_pain_max") == 2


@pytest.mark.parametrize(("text", "problem"), [
    (RUNS.replace('workout = "threshold"\n', ""), "a run lineage names the workout"),
    (RUNS.replace("reserve_min = 1\n", ""), "a run lineage needs reserve_min"),
    (RUNS.replace('workout = "threshold"', 'workout = "long"'), "'long' unknown"),
])
def test_a_run_lineage_missing_what_it_needs_is_refused(text, problem):
    with pytest.raises(rulebook.RulebookError, match=problem):
        book_with(PROGRESSION + text)


def test_an_exercise_lineage_names_no_workout():
    entry = ('\n[[progression.lineages]]\nslug = "a"\nfamily = "strength-time"\n'
             'workout = "threshold"\nseconds = { minimum = 15, maximum = 30, step = 5 }\n')
    with pytest.raises(rulebook.RulebookError, match="a run lineage names the workout"):
        book_with(PROGRESSION + entry)


# ------------------------------------------------------------------ exposures

FAMILY = default()["intervals"]


def lineage():
    return book_with(PROGRESSION + RUNS).progression.of("threshold-reps")


def test_the_dose_is_the_planned_sessions_and_the_readings_the_ledgers():
    plans = progression_runs.Plans(Store({"2030-W10": [threshold(day(1), 4)]}, {}))
    rows = [morning(day(-5)), run(day(1), 11), morning(day(2)), easy(day(2)), easy(day(3)),
            easy(day(5))]
    store = Store({"2030-W10": [threshold(day(1), 4)]}, {11: [{"owner": "athlete"}]})
    found = progression_runs.exposures(store, rows, lineage(), FAMILY, plans, [ZONE], None)
    assert [exposure.on for exposure in found] == [day(1)]
    assert found[0].prescribed == Dose.of(reps=4, rep_seconds=300, recovery_seconds=120)
    readings = {reading.signal: reading.value for reading in found[0].readings}
    assert readings[Signal.EXECUTION_GAPS] == 1
    assert readings[Signal.RESERVE_REPS] == 1
    assert readings[Signal.RPE_OVER_RANGE] is None, "no history, no expected range"


def test_a_session_run_as_its_dose_expects_rises_one_repetition():
    rows = [morning(day(-5)), run(day(1), 11), morning(day(2)), easy(day(2)), easy(day(3)),
            easy(day(5))]
    store = Store({"2030-W10": [threshold(day(1), 4)]}, {11: []})
    history = expected_rpe.History(
        rows, lambda row: None, lambda day: None,
        expected_rpe.Settings(26, 3, 5, 1.0, 1.5, 2.0))
    history.expectation = lambda row: expected_rpe.Expectation(7.0, 6, 8, 5, 0.3)  # type: ignore[method-assign]
    found = progression_runs.exposures(store, rows, lineage(), FAMILY,
                                       progression_runs.Plans(store), [ZONE], history)
    readings = {reading.signal: reading.value for reading in found[0].readings}
    assert readings[Signal.RPE_OVER_RANGE] == -1
    proposal = propose(FAMILY, lineage(), found, current=None, today=day(7))
    assert proposal.move == Move.UP
    assert proposal.dose == Dose.of(reps=5, rep_seconds=300, recovery_seconds=120)


def test_a_session_without_a_typed_dose_is_no_exposure_and_the_plan_gives_the_current_one():
    store = Store({"2030-W10": [threshold(day(1), None, seconds=None, recovery=None)],
                   "2030-W11": [threshold(day(8), 5)]}, {})
    plans = progression_runs.Plans(store)
    assert progression_runs.exposures(store, [run(day(1), 11)], lineage(), FAMILY, plans,
                                      [ZONE], None) == []
    assert progression_runs.current_dose(FAMILY, lineage(), plans, "2030-W11") == Dose.of(
        reps=5, rep_seconds=300, recovery_seconds=120)


# ------------------------------------------------------------------ vetoes

def test_a_gate_that_did_not_open_vetoes_a_rise(monkeypatch):
    monkeypatch.setattr(progression_strength.race_model, "upcoming", lambda store, today: None)
    book = book_with(PROGRESSION + RUNS)
    monkeypatch.setattr(rulebook, "current", lambda: book)
    zones = [ledger.PainZone(ZONE, "Genou")]
    found = progression_runs.vetoes(Store({}, {}), book, [easy(day(-6), pain=3.0)], "2030-W10",
                                    day(0), zones)
    assert [veto.kind for veto in found] == [VetoKind.GATE]
    assert "2030-W09" in found[0].fact
