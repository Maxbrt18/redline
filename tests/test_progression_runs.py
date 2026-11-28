"""The run lineages: declared in the rulebook, their dose read from the plan, proposed."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from test_progression_strength import PROGRESSION, book_with
from test_weekly_plan import revision, session

from redline.analysis import expected_rpe, ledger, progression_runs, progression_strength
from redline.analysis.progression import StepTally, propose
from redline.model import rulebook
from redline.model.progression import (
    Bound,
    Dose,
    Move,
    RiseOutcome,
    Signal,
    Veto,
    VetoKind,
    default,
)
from redline.model.vocabulary import OverrideReason

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

    def cycle_for_week(self, week):
        return None

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
    monkeypatch.setattr(progression_strength.race_model, "active_goal", lambda store: None)
    book = book_with(PROGRESSION + RUNS)
    monkeypatch.setattr(rulebook, "current", lambda: book)
    zones = [ledger.PainZone(ZONE, "Genou")]
    found = progression_runs.vetoes(Store({}, {}), book, [easy(day(-6), pain=3.0)], "2030-W10",
                                    day(0), zones)
    assert [veto.kind for veto in found] == [VetoKind.GATE]
    assert "2030-W09" in found[0].fact


# ------------------------------------------------------------------ the bounded override

CEILING = Dose.of(reps=5, rep_seconds=300, recovery_seconds=120)


class Written(Store):
    def prediction_rows(self):
        return [{"prediction_key": "w11-seuil", "status": "open"},
                {"prediction_key": "w09-old", "status": "resolved"}]

    def pain_zones(self):
        return []


@pytest.fixture
def ruled(monkeypatch):
    """The lineage's ceiling is 5 x 5 min, recovery 2 min; `vetoed` sets the vetoes."""
    from redline.model.progression import Proposal

    state = {"vetoes": ()}
    monkeypatch.setattr(ledger, "parse_ledger", lambda store: [])
    monkeypatch.setattr(progression_runs, "proposals", lambda store, book, today, week: [
        Proposal("threshold-reps", "intervals", Move.UP, Move.UP, "tolerated", CEILING,
                 CEILING, CEILING)])
    monkeypatch.setattr(progression_runs, "vetoes", lambda *args: state["vetoes"])

    def check(*sessions, before=(), plans=None):
        store = Written(plans or {}, {})
        return progression_runs.override_errors(
            store, book_with(PROGRESSION + RUNS), "2030-W11", revision(sessions=list(sessions)),
            revision(sessions=list(before)) if before else None, day(7))

    check.state = state
    return check


def written(reps, seconds=300, recovery=120, **extra):
    return {**threshold(day(10), reps, seconds=seconds, recovery=recovery), **extra}


OVERRIDE = {"override_reason": "tolerated_more", "override_prediction": "w11-seuil"}


def test_a_dose_at_its_ceiling_declares_no_override(ruled):
    assert ruled(written(5)) == []
    assert ruled(written(4)) == [], "a plan may always prescribe less"
    assert "no override at or under the ceiling" in ruled(written(5, **OVERRIDE))[0]


def test_one_step_above_the_ceiling_is_the_bounded_override(ruled):
    assert ruled(written(6, **OVERRIDE)) == []
    assert "declare override_reason" in ruled(written(6))[0]
    assert "more than one step above" in ruled(written(6, seconds=360, **OVERRIDE))[0]


def test_the_override_needs_an_open_prediction_and_no_veto(ruled):
    late = {**OVERRIDE, "override_prediction": "w09-old"}
    assert "no open prediction w09-old" in ruled(written(6, **late))[0]
    ruled.state["vetoes"] = (Veto(VetoKind.TAPER, "taper_weeks", "race 2030-04-06"),)
    assert "under a veto: taper_weeks race 2030-04-06" in ruled(written(6, **OVERRIDE))[0]


def test_never_two_overrides_in_a_row_on_a_lineage(ruled):
    earlier = {**threshold(day(3), 5), **OVERRIDE}
    found = ruled(written(6, **OVERRIDE), plans={"2030-W10": [earlier]})
    assert len(found) == 1 and "never twice in a row" in found[0]
    plain = threshold(day(3), 5)
    assert ruled(written(6, **OVERRIDE), plans={"2030-W10": [plain]}) == []


def test_a_dose_carried_unchanged_is_not_checked_again(ruled):
    assert ruled(written(7), before=[written(7)]) == []
    assert ruled(written(7), before=[written(6)]) != []


# ------------------------------------------------------------------ the tally

HILLS = """
[[progression.lineages]]
slug = "hill-reps"
family = "intervals"
workout = "hills"
reps = { minimum = 6, maximum = 10, step = 1 }
rep_seconds = { minimum = 60, maximum = 90, step = 15 }
recovery_seconds = { minimum = 60, maximum = 120, step = 30 }
"""


def hills(on: dt.date, reps: int) -> dict:
    return session(f"hil-{on.isoformat()}", on.isoformat(), "hills", reps=reps,
                   rep_seconds=60, recovery_seconds=120)


class Tallied(Store):
    def __init__(self, plans, verdicts, predictions=()):
        super().__init__(plans, verdicts)
        self.predictions = list(predictions)

    def prediction_rows(self):
        return self.predictions


def answered(on: dt.date, activity: int, kind: str = "threshold") -> list[ledger.Row]:
    """A quality session, its next morning and the easy runs after it, all clean."""
    quality = run(on, activity)
    if kind != "threshold":
        quality = ledger.Row(on, kind, idempotency_key=f"strava:{activity}", km=10,
                             hours=1.0, rpe=7.0, reserve=1.0, pains={ZONE: 0.5})
    return [quality, morning(on + dt.timedelta(days=1)), easy(on + dt.timedelta(days=1)),
            easy(on + dt.timedelta(days=2)), easy(on + dt.timedelta(days=4))]


@pytest.fixture
def reviewed(monkeypatch):
    """The reviews of the declared lineages over `rows`, every RPE within its range."""
    def within(store, rows, config):
        history = expected_rpe.History(rows, lambda row: None, lambda day: None, config)
        history.expectation = lambda row: expected_rpe.Expectation(7.0, 6, 8, 5, 0.3)  # type: ignore[method-assign]
        return history

    monkeypatch.setattr(expected_rpe, "settings",
                        lambda: expected_rpe.Settings(26, 3, 5, 1.0, 1.5, 2.0))
    monkeypatch.setattr(expected_rpe, "history", within)
    monkeypatch.setattr(ledger, "pain_zones", lambda store: [ledger.PainZone(ZONE, "Genou")])

    def review(store, rows, declared=RUNS):
        monkeypatch.setattr(ledger, "parse_ledger", lambda _: rows)
        return progression_runs.reviews(store, book_with(PROGRESSION + declared))

    return review


def test_the_tally_reads_each_rise_and_each_override(reviewed):
    plans = {"2030-W10": [threshold(day(1), 4)], "2030-W11": [threshold(day(8), 5)],
             "2030-W12": [{**threshold(day(15), 6), "override_reason": "athlete_choice",
                           "override_prediction": "w12-seuil"}]}
    store = Tallied(plans, {11: [], 12: [], 13: [{"owner": "athlete"}]},
                    [{"prediction_key": "w12-seuil", "status": "resolved", "outcome": False}])
    rows = [*answered(day(1), 11), *answered(day(8), 12), *answered(day(15), 13)]
    [found] = reviewed(store, rows)
    assert found.tally.tolerated == (day(8),)
    assert found.tally.failed == (day(15),)
    assert found.tally.suggestion is None, "two rises read say nothing of the step"
    [override] = found.overrides
    assert override.on == day(15) and override.prediction == "w12-seuil"
    assert override.followed == RiseOutcome.FAILED and override.borne_out is False


def test_two_lineages_rising_the_same_week_are_unread_for_both(reviewed, monkeypatch):
    book = rulebook.current()
    monkeypatch.setattr(rulebook, "current", lambda: dataclasses.replace(
        book, parameters=dataclasses.replace(book.parameters, quality_sessions_per_week=2)))
    plans = {"2030-W10": [threshold(day(1), 4), hills(day(3), 6)],
             "2030-W11": [threshold(day(8), 5), hills(day(10), 7)]}
    store = Tallied(plans, {11: [], 12: [], 21: [], 22: []})
    rows = [*answered(day(1), 11), *answered(day(3), 21, "hills"),
            *answered(day(8), 12), *answered(day(10), 22, "hills")]
    found = {review.lineage: review.tally for review in reviewed(store, rows, RUNS + HILLS)}
    assert found["threshold-reps"].unread == (day(8),)
    assert found["hill-reps"].unread == (day(10),)
    assert not found["threshold-reps"].tolerated and not found["hill-reps"].tolerated


def test_the_tally_reads_as_the_coach_reads_it():
    tally = StepTally((day(8), day(22)), (day(15),), (), ())
    override = progression_runs.Override(day(15), OverrideReason.ATHLETE_CHOICE, "w12-seuil",
                                         RiseOutcome.FAILED, None)
    lines = progression_runs.render_reviews([
        progression_runs.Review("threshold-reps", tally, (override,)),
        progression_runs.Review("hill-reps", StepTally((), (), (), ()), ())])
    assert lines[1] == ("- threshold-reps : tolérées 2 (12/03, 26/03) ; ratées 1 (19/03) · "
                        "trop peu de hausses lues pour juger le cran (3 sur 5)")
    assert lines[2] == ("  dérogation du 19/03 (choix de l'athlète) : ratée, prédiction pas "
                        "encore tranchée")
    assert lines[3] == "- hill-reps : aucune hausse courue"
    assert progression_runs.render_reviews([]) == []
