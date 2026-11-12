"""A long run's effort accumulates with its duration: no fixed RPE, its own history."""

from __future__ import annotations

import datetime as dt

from conftest import TEST_VAULT

from redline.analysis import comparable, ledger, reorganize
from redline.model import debrief, program, weekly_plan

SUNDAY = dt.date(2026, 3, 1)


def long_run(days_ago: int, minutes: int, rpe: float | None) -> ledger.Row:
    return ledger.Row(SUNDAY - dt.timedelta(days=days_ago), "long", km=minutes / 6,
                      hours=minutes / 60, rpe=rpe)


def test_comparables_keep_the_latest_long_runs_of_a_close_duration():
    rows = [long_run(7, 160, 7), long_run(14, 100, 5), long_run(21, 150, 8),
            long_run(28, 170, None), long_run(35, 165, 6), long_run(0, 160, 9)]
    found = comparable.comparables(rows, "long", 160, SUNDAY)
    assert [item.date for item in found] == [SUNDAY - dt.timedelta(days=days)
                                             for days in (7, 21, 35)], \
        "same kind, duration within the rulebook share, declared RPE, before the day, newest first"
    assert comparable.typical_rpe(rows, "long", 160, SUNDAY) == 7


def test_comparables_stop_at_the_rulebook_count():
    rows = [long_run(7 * week, 120, 6) for week in range(1, 10)]
    assert len(comparable.comparables(rows, "long", 120, SUNDAY)) == 5


def test_a_kind_whose_effort_does_not_accumulate_has_no_comparables():
    rows = [ledger.Row(SUNDAY - dt.timedelta(days=2), "easy", hours=1.0, rpe=3)]
    assert comparable.comparables(rows, "easy", 60, SUNDAY) == []
    assert comparable.typical_rpe([], "long", 150, SUNDAY) is None


def test_a_long_run_has_no_rpe_band_even_when_one_is_stored():
    assert weekly_plan.rpe_band({"workout": "long", "rpe_min": 3, "rpe_max": 4}) is None
    assert weekly_plan.rpe_band({"workout": "easy", "rpe_min": 2, "rpe_max": 3}) == (2, 3)
    assert weekly_plan.rpe_band({"workout": "easy", "rpe_max": 3}) == (None, 3)
    assert weekly_plan.rpe_band({"workout": "easy"}) is None


def test_a_new_revision_refuses_an_rpe_band_given_to_a_long_run_but_carries_a_stored_one():
    def revision(**band) -> dict:
        return {"sessions": [
            {"id": "sun-long", "kind": "run", "workout": "long", "status": "planned", **band}]}

    def refused(previous, current) -> bool:
        return any("sun-long" in problem and "rpe" in problem
                   for problem in weekly_plan.accumulating_band_errors(previous, current))

    assert refused(None, revision(rpe_max=4))
    assert refused(revision(), revision(rpe_max=4))
    assert refused(revision(rpe_min=3, rpe_max=4), revision(rpe_max=5))
    assert not refused(revision(rpe_min=3, rpe_max=4), revision(rpe_min=3, rpe_max=4)), \
        "a week planned before the rule must stay revisable"
    assert not refused(None, revision())


def test_the_debrief_compares_no_rpe_on_a_long_run():
    row = ledger.Row(SUNDAY, "long", km=25.0, hours=2.8, rpe=8)
    assert debrief.planned_vs_done(row, {"workout": "long", "rpe_max": 4}) == []
    assert debrief.planned_vs_done(row, {"workout": "easy", "rpe_max": 4}) != []


def test_the_workout_notes_give_no_rpe_target_to_a_long_run(tmp_path):
    long_note = tmp_path / "coaching" / "workouts" / "long-run.md"
    long_note.parent.mkdir(parents=True)
    long_note.write_text("---\ntype: workout\nkinds: [long]\ntarget_rpe: 4\n---\n")
    assert program.rpe_targets(tmp_path) == {}
    assert ("easy", None) in program.rpe_targets(TEST_VAULT)


def test_reorganize_weighs_a_long_run_by_its_history_else_the_hardest_target():
    sessions = [{"id": "sun-long", "title": "long", "workout": "long", "kind": "run",
                 "date": SUNDAY.isoformat(), "demanding": True, "status": "planned",
                 "duration_cap_min": 150, "rpe_max": 4}]
    targets = {("threshold", None): 7.0, ("easy", None): 3.0}

    def load(typical) -> float:
        return reorganize.items(sessions, [], None, targets, typical)[0].load

    assert load(lambda kind, minutes: 8.0) == 150 * 8.0
    assert load(lambda kind, minutes: None) == 150 * 7.0
    assert load(None) == 150 * 7.0, "the stored band of 4 is never read"
