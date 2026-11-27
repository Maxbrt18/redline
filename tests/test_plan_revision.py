"""The write gate's guards: only an unavailable measurement lets a revision through
unchecked; a declared frame that cannot be read, or a failing check, refuses."""

from __future__ import annotations

import pytest
import trajectory
from sqlalchemy.exc import OperationalError

from redline.analysis import ledger, plan_engine
from redline.model import iso_week
from redline.model.errors import ConfigError, ExternalError, InputError, StorageError
from redline.service import plan_revision

WEEK = "2030-W49"
WRITTEN_ON = trajectory.review_day(WEEK)


def _revision(volume_km=30.0):
    return trajectory.revision(WEEK, volume_km, 12, WRITTEN_ON)


def _store(goal=trajectory.HALF):
    store = trajectory.Store(goal, trajectory.RACE_ON)
    for back in range(1, trajectory.HISTORY_WEEKS + 1):
        trajectory.run_week(store, iso_week.shift(WEEK, -back), 30, 14)
    return store


@pytest.mark.parametrize("failure", [
    OSError("down"), StorageError("refused"), ExternalError("timeout"),
    OperationalError("SELECT", {}, Exception("connection lost")),
], ids=["os", "storage", "external", "database"])
def test_an_unavailable_measurement_costs_the_check_with_a_warning(monkeypatch, failure):
    def broken(store):
        raise failure
    monkeypatch.setattr(ledger, "parse_ledger", broken)
    warned = []
    assert plan_revision.skeleton_errors(_store(), WEEK, _revision(), None, warned.append) == []
    assert [type(failure).__name__ in message for message in warned] == [True]


def test_a_frame_that_cannot_be_read_refuses(monkeypatch):
    def misconfigured(store, today):
        raise ConfigError("rulebook.toml declares no skeleton thresholds")
    monkeypatch.setattr(plan_engine, "chosen", misconfigured)
    warned = []
    [refusal] = plan_revision.skeleton_errors(_store(), WEEK, _revision(), None, warned.append)
    assert refusal.startswith(f"{WEEK} revision 1: the skeleton check cannot read its frame")
    assert "no skeleton thresholds" in refusal and warned == []


def test_a_check_that_fails_on_a_defect_refuses_and_names_it(monkeypatch):
    def defect(store, today):
        raise KeyError("peak_volume_km")
    monkeypatch.setattr(plan_engine, "chosen", defect)
    [refusal] = plan_revision.skeleton_errors(_store(), WEEK, _revision(), None, print)
    assert "the skeleton check failed (KeyError" in refusal


def test_each_guard_closes_the_same_way(monkeypatch):
    def misconfigured(*args, **kwargs):
        raise InputError("unreadable")
    monkeypatch.setattr(ledger, "parse_ledger", misconfigured)
    monkeypatch.setattr(plan_revision, "pain_rule_errors", misconfigured)
    store, revision = _store(), _revision()
    taper_week = trajectory.revision("2031-W14", 10, 0, WRITTEN_ON)
    assert plan_revision.taper_errors(store, "2031-W14", taper_week, print)
    assert plan_revision.pain_errors(store, WEEK, revision, None, print)
    monkeypatch.setattr("redline.analysis.progression_runs.override_errors", misconfigured)
    assert plan_revision.progression_errors(store, WEEK, revision, None, print)
    monkeypatch.setattr("redline.analysis.progression_fuelling.ceiling_errors",
                        misconfigured)
    assert plan_revision.gut_errors(store, WEEK, revision, print)


def test_a_race_without_a_chosen_skeleton_binds_nothing():
    unchosen = {key: value for key, value in trajectory.HALF.items() if key != "skeleton"}
    assert plan_revision.skeleton_errors(_store(unchosen), WEEK, _revision(400), None,
                                         print) == []


def test_a_chosen_skeleton_whose_context_lost_a_key_refuses():
    broken = {**trajectory.HALF, "peak_volume_km": "fifty"}
    [refusal] = plan_revision.skeleton_errors(_store(broken), WEEK, _revision(), None, print)
    assert "keys that cannot be read (peak_volume_km)" in refusal
    assert "redline fact-write deadline" in refusal


def test_a_chosen_skeleton_without_its_peaks_refuses():
    incomplete = {key: value for key, value in trajectory.HALF.items()
                  if key != "peak_long_run_km"}
    with pytest.raises(InputError, match="peak_long_run_km"):
        plan_engine.chosen(_store(incomplete), WRITTEN_ON)
    assert plan_revision.skeleton_errors(_store(incomplete), WEEK, _revision(), None, print)


def test_a_readable_chosen_skeleton_still_bounds_the_week():
    assert plan_revision.skeleton_errors(_store(), WEEK, _revision(30), None, print) == []
    [refusal] = plan_revision.skeleton_errors(_store(), WEEK, _revision(400), None, print)
    assert "volume_target_km: 400 km above" in refusal
