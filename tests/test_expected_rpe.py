"""The RPE a session should cost: computed from its dose, calibrated on the athlete."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from redline.analysis import easy_load, expected_rpe, ledger, reorganize
from redline.model import rulebook
from redline.model.vocabulary import RpeSide

DAY = dt.date(2030, 11, 17)
#: 6:00/km, in metres per second.
REFERENCE = 1000 / 360
CONFIG = expected_rpe.Settings(weeks=26, min_sessions=3, settled_sessions=5, width=1.0,
                               width_unsettled=1.5, exponent=2.0)


def run(days_ago: int, km: float, rpe: float | None = None, *, kind: str = "long",
        dplus: int = 0, minutes: float | None = None, key: str = "") -> ledger.Row:
    hours = (minutes if minutes is not None else km * 6) / 60
    return ledger.Row(DAY - dt.timedelta(days=days_ago), kind, idempotency_key=key, km=km,
                      hours=hours, dplus=dplus, rpe=rpe)


def history(rows, *, reference=REFERENCE, profiles=None) -> expected_rpe.History:
    profiles = profiles or {}
    return expected_rpe.History(rows, lambda row: profiles.get(row.idempotency_key),
                                lambda day: reference, CONFIG)


def law(dose: float) -> float:
    """The fictional athlete's law: RPE = -5 + 3.4 ln(dose)."""
    return -5 + 3.4 * math.log(dose)


def test_the_fit_recovers_the_athletes_law_from_their_declared_rpe():
    points = [(dose, law(dose)) for dose in (12.0, 14.0, 16.5, 19.0, 27.0)]
    model = expected_rpe.fit(points, CONFIG)
    assert model.slope == pytest.approx(3.4)
    assert model.intercept == pytest.approx(-5)
    assert model.residual == pytest.approx(0, abs=1e-9)
    assert model.sessions == 5


def test_the_range_is_three_integers_around_the_expectation_once_settled():
    model = expected_rpe.Model(intercept=0, slope=1, sessions=5, residual=0.3)
    for expected, (low, high) in ((3.3, (2, 4)), (4.9, (4, 6)), (3.7, (3, 5)), (6.0, (5, 7))):
        found = expected_rpe.expect(model, expected_rpe.Dose(math.exp(expected)), CONFIG)
        assert (found.low, found.high) == (low, high), expected
    found = expected_rpe.expect(model, expected_rpe.Dose(math.exp(9.6)), CONFIG)
    assert found.high == 10, "the scale stops at 10"


def test_the_range_widens_while_a_kind_has_few_declared_rpe():
    model = expected_rpe.Model(intercept=0, slope=1, sessions=4, residual=0.3)
    found = expected_rpe.expect(model, expected_rpe.Dose(math.exp(4.3)), CONFIG)
    assert (found.low, found.high) == (3, 6)


def test_no_model_below_the_minimum_and_a_flat_one_when_the_doses_tell_no_slope():
    assert expected_rpe.fit([(10, 3), (20, 5)], CONFIG) is None
    same = expected_rpe.fit([(10, 3), (10, 4), (10, 5)], CONFIG)
    assert (same.slope, same.intercept) == (0, 4)
    reversed_ = expected_rpe.fit([(10, 6), (15, 5), (20, 4)], CONFIG)
    assert reversed_.slope == 0, "more dose never costs less"
    assert reversed_.intercept == pytest.approx(5)


def test_a_run_counts_its_gain_and_its_intensity():
    flat = expected_rpe.dose_of(run(0, 20), None, REFERENCE, 2.0)
    hilly = expected_rpe.dose_of(run(0, 20, dplus=500, minutes=150), None, REFERENCE, 2.0)
    assert flat.value == pytest.approx(20), "a flat run at the reference pace: its kilometres"
    assert hilly.effort_km == 25, "100 m of gain are worth a kilometre"
    assert hilly.intensity == 1.0, "a hilly run without streams is never judged slow"
    fast = expected_rpe.dose_of(run(0, 10, kind="easy", minutes=50), None, REFERENCE, 2.0)
    assert fast.intensity == pytest.approx(1.2)
    assert fast.value == pytest.approx(10 * 1.2 ** 2)


def test_a_climb_run_slowly_is_counted_at_its_flat_equivalent_speed():
    shape = easy_load.Profile((easy_load.Stretch(3000, REFERENCE * 1.1),), 8000, 400, 400)
    dose = expected_rpe.dose_of(run(0, 8, kind="easy", dplus=400, minutes=60), shape,
                                REFERENCE, 2.0)
    assert dose.intensity == pytest.approx(1.1)
    assert dose.value == pytest.approx(12 * 1.1 ** 2)


def test_without_a_reference_only_an_easy_run_has_a_dose():
    assert expected_rpe.dose_of(run(0, 12), None, None, 2.0).intensity == 1.0
    assert expected_rpe.dose_of(run(0, 12, kind="threshold"), None, None, 2.0) is None
    strength = ledger.Row(DAY, "strength", hours=0.75, rpe=6, routine="a")
    assert expected_rpe.dose_of(strength, None, None, 2.0).value == pytest.approx(45)


def test_a_session_is_read_against_the_earlier_sessions_of_its_own_kind():
    past = [run(days, km, law(km)) for days, km in ((70, 12), (56, 14), (42, 16.5),
                                                    (28, 19), (14, 27))]
    noise = [run(7, 5, 9, kind="easy"), run(3, 30, 1)]
    today = run(0, 28)
    found = history([*past, *noise, today]).expectation(today)
    assert found.sessions == 6, "the long run of three days ago counts, the easy one not"
    later = history([*past, today]).expectation(today)
    assert later.expected == pytest.approx(law(28))
    assert (later.low, later.high) == (5, 7)
    assert later.side(6) is RpeSide.WITHIN
    assert later.side(8) is RpeSide.ABOVE
    assert later.side(4) is RpeSide.BELOW
    assert later.side(None) is RpeSide.UNKNOWN


def test_the_window_bounds_the_history():
    old = [run(days, km, law(km)) for days, km in ((400, 12), (390, 16), (380, 20))]
    today = run(0, 18)
    assert history([*old, today]).expectation(today) is None


def test_strength_is_calibrated_routine_by_routine():
    rows = [ledger.Row(DAY - dt.timedelta(days=d), "strength", hours=0.75, rpe=r, routine=x)
            for d, r, x in ((20, 6, "a"), (13, 6, "a"), (6, 6, "a"), (10, 3, "b"))]
    today = ledger.Row(DAY, "strength", hours=0.75, routine="a")
    assert history([*rows, today]).expectation(today).expected == 6
    other = ledger.Row(DAY, "strength", hours=0.75, routine="b")
    assert history([*rows, other]).expectation(other) is None


def test_a_prescribed_easy_or_long_run_has_its_own_expectation():
    past = [run(days, km, law(km)) for days, km in ((70, 12), (56, 14), (42, 16.5),
                                                    (28, 19), (14, 27))]
    found = history(past).prescribed(
        {"workout": "long", "distance_cap_km": 20, "dplus_cap_m": 400}, DAY)
    assert found.expected == pytest.approx(law(24))
    timed = history(past).prescribed({"workout": "long", "duration_cap_min": 120}, DAY)
    assert timed.expected == pytest.approx(law(20)), "two hours at the reference easy pace"
    assert history(past).prescribed(
        {"workout": "long", "distance_cap_km": 20, "structure": "efforts"}, DAY) is None
    assert history(past).prescribed({"workout": "threshold"}, DAY) is None


def test_a_planned_session_without_a_typed_dose_weighs_its_kinds_median():
    rows = [run(days, 8, rpe, kind="threshold") for days, rpe in ((21, 6), (14, 7), (7, 8))]
    assert history(rows).planned({"workout": "threshold"}, DAY) == 7
    assert history(rows[:2]).planned({"workout": "threshold"}, DAY) is None


def test_the_method_is_declared_whole_or_not_at_all(monkeypatch):
    assert expected_rpe.settings() is not None
    book = rulebook.current()
    stripped = rulebook.Parameters(**{**vars(book.parameters), "rpe_range_width": None})
    monkeypatch.setattr(rulebook, "current", lambda: type(book)(
        stripped, book.scales, book.transitions, book.rules, book.source, book.progression))
    assert expected_rpe.settings() is None


def test_reorganize_weighs_a_session_by_its_expected_rpe_and_nothing_without():
    sessions = [{"id": "sun-long", "title": "long", "workout": "long", "kind": "run",
                 "date": DAY.isoformat(), "demanding": True, "status": "planned",
                 "duration_cap_min": 150}]
    assert reorganize.items(sessions, [], None, lambda session: 5.5)[0].load == 150 * 5.5
    assert reorganize.items(sessions, [], None, lambda session: None)[0].load == 0
    assert reorganize.items(sessions, [], None)[0].load == 0
