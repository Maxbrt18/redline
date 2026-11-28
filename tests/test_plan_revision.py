"""The write gate's guards: only an unavailable measurement lets a revision through
unchecked; a declared frame that cannot be read, or a failing check, refuses."""

from __future__ import annotations

import pytest
import trajectory
from sqlalchemy.exc import OperationalError

from redline.analysis import ledger, taper
from redline.model import iso_week
from redline.model.errors import ConfigError, ExternalError, InputError, StorageError
from redline.service import plan_revision

WEEK = "2030-W49"
WRITTEN_ON = trajectory.review_day(WEEK)
#: The race week of the goal: in its taper.
TAPER_WEEK = "2031-W14"


def _revision(volume_km=30.0):
    return trajectory.revision(WEEK, volume_km, 12, WRITTEN_ON)


def _taper():
    return trajectory.revision(TAPER_WEEK, 10, 0, WRITTEN_ON)


def _store(goal=trajectory.HALF):
    store = trajectory.Store(goal, trajectory.RACE_ON)
    for back in range(1, trajectory.HISTORY_WEEKS + 1):
        trajectory.run_week(store, iso_week.shift(WEEK, -back), 30, 14)
    # The preparation that holds the goal: its taper is the one the gate reads.
    trajectory.take(store, trajectory.window_start())
    return store


@pytest.mark.parametrize("failure", [
    OSError("down"), StorageError("refused"), ExternalError("timeout"),
    OperationalError("SELECT", {}, Exception("connection lost")),
], ids=["os", "storage", "external", "database"])
def test_an_unavailable_measurement_costs_the_check_with_a_warning(monkeypatch, failure):
    def broken(store):
        raise failure
    store = _store()
    monkeypatch.setattr(ledger, "parse_ledger", broken)
    warned = []
    assert plan_revision.taper_errors(store, TAPER_WEEK, _taper(), warned.append) == []
    assert [type(failure).__name__ in message for message in warned] == [True]


def test_a_frame_that_cannot_be_read_refuses(monkeypatch):
    def misconfigured(store, week):
        raise ConfigError("rulebook.toml declares no taper")
    monkeypatch.setattr(taper, "race_window", misconfigured)
    warned = []
    [refusal] = plan_revision.taper_errors(_store(), TAPER_WEEK, _taper(), warned.append)
    assert refusal.startswith(f"{TAPER_WEEK} revision 1: the taper check cannot read its frame")
    assert "declares no taper" in refusal and warned == []


def test_a_check_that_fails_on_a_defect_refuses_and_names_it(monkeypatch):
    def defect(store, week):
        raise KeyError("taper_weeks")
    monkeypatch.setattr(taper, "race_window", defect)
    [refusal] = plan_revision.taper_errors(_store(), TAPER_WEEK, _taper(), print)
    assert "the taper check failed (KeyError" in refusal


def test_each_guard_closes_the_same_way(monkeypatch):
    def misconfigured(*args, **kwargs):
        raise InputError("unreadable")
    store, revision = _store(), _revision()
    monkeypatch.setattr(ledger, "parse_ledger", misconfigured)
    monkeypatch.setattr(plan_revision, "pain_rule_errors", misconfigured)
    assert plan_revision.taper_errors(store, TAPER_WEEK, _taper(), print)
    assert plan_revision.pain_errors(store, WEEK, revision, None, print)
    monkeypatch.setattr("redline.analysis.progression_runs.override_errors", misconfigured)
    assert plan_revision.progression_errors(store, WEEK, revision, None, print)
    monkeypatch.setattr("redline.analysis.progression_fuelling.ceiling_errors",
                        misconfigured)
    assert plan_revision.gut_errors(store, WEEK, revision, print)
