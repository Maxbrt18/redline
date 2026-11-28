"""The draft of a week: the engine's revision from the cycle version, which always passes
the gate, and which the coach amends with evidence."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
import trajectory

from redline.analysis import weekly_draft
from redline.model import iso_week
from redline.model.progression import Dose
from redline.model.weekly_plan import draft_change_errors
from redline.service import plan_revision

#: The coach's decision, which the draft leaves to them.
JUDGMENT = {"by": "judgment", "alternatives": [
    {"option": "une semaine plus légère", "rejected_because": "rien ne l'appelle"}]}


def _lived():
    """A preparation lived to its window, each week of it ready to be drafted."""
    return trajectory.simulate(through=trajectory.window_start()).store


def _drafts():
    store = _lived()
    for week in store.cycle.trame.weeks:
        yield pytest.param(store, week.label, id=week.label)


@pytest.mark.parametrize(("store", "week"), list(_drafts()))
def test_every_draft_of_the_preparation_passes_the_gate(store, week):
    sunday = iso_week.monday(week) - dt.timedelta(days=1)
    found = weekly_draft.frame(store, week, sunday)
    assert found is not None
    stamp = f"{sunday.isoformat()}T18:00:00+01:00"
    revision = {**weekly_draft.draft(found, stamp), "decision": JUDGMENT}
    warned: list[str] = []
    plan_revision.checked(store, week, revision, None, stamp=stamp, warn=warned.append)
    assert not warned



STAMP = "2031-01-04T18:00:00+01:00"


def _frame(week=None):
    store = _lived()
    label = week or next(w.label for w in store.cycle.trame.weeks
                         if any(key.role == "key" for key in store.cycle.keys_of(w.label)))
    found = weekly_draft.frame(store, label, iso_week.monday(label) - dt.timedelta(days=1))
    assert found is not None
    return store, found


def test_a_key_session_is_linked_at_the_trame_s_dose_or_its_lineage_s_when_lower():
    _, found = _frame()
    key = next(key for key in found.keys if key.dose is not None)
    drafted = next(session for session in weekly_draft.draft(found, STAMP)["sessions"]
                   if session.get("key_position") == key.position)
    assert drafted["workout"] == key.workout and drafted["reps"] == key.dose.get("reps")
    lower = Dose.of(reps=1, rep_seconds=60, recovery_seconds=60)
    held = dataclasses.replace(found, lineages={key.workout: lower})
    drafted = next(session for session in weekly_draft.draft(held, STAMP)["sessions"]
                   if session.get("key_position") == key.position)
    assert (drafted["reps"], drafted["rep_seconds"]) == (1, 60), "the lineage stands lower"
    higher = dataclasses.replace(found, lineages={key.workout: Dose.of(reps=20, rep_seconds=600)})
    drafted = next(session for session in weekly_draft.draft(higher, STAMP)["sessions"]
                   if session.get("key_position") == key.position)
    assert drafted["reps"] == key.dose.get("reps"), "never above the trame"


def test_the_long_run_the_easy_runs_and_the_volume_come_from_the_week():
    _, found = _frame()
    for runs in (1, 3):
        drafted = weekly_draft.draft(dataclasses.replace(found, easy_runs=runs), STAMP)
        sessions = drafted["sessions"]
        [long_run] = [session for session in sessions if session["workout"] == "long"]
        assert long_run["distance_target_km"] == found.bound.long_run_km
        easy = [session for session in sessions if session["workout"] == "easy"]
        assert 1 <= len(easy) <= runs
        assert drafted["volume_target_km"] <= found.bound.volume_km


def test_the_strength_of_the_week_before_is_carried_and_the_race_week_runs_the_race():
    _, found = _frame()
    monday = iso_week.monday(found.week) - dt.timedelta(days=7)
    strength = {"id": "w-mar-renfo-a", "title": "Renfo A", "date": (monday + dt.timedelta(days=1)).isoformat(),
                "kind": "strength", "workout": "strength", "routine": "a", "status": "planned",
                "demanding": False, "strength": {}, "sources": ["[[strength]]"]}
    drafted = weekly_draft.draft(dataclasses.replace(found, strength=(strength,)), STAMP)
    [carried] = [session for session in drafted["sessions"] if session["kind"] == "strength"]
    assert carried["routine"] == "a" and carried["id"].startswith(found.week.lower())
    race_day = iso_week.monday(found.week) + dt.timedelta(days=6)
    drafted = weekly_draft.draft(dataclasses.replace(found, race=(race_day, 21.1, "Semi")), STAMP)
    assert [session["workout"] for session in drafted["sessions"]].count("race") == 1
    assert "long" not in [session["workout"] for session in drafted["sessions"]]


def _amended(draft, **changes):
    current = {**draft, "decision": JUDGMENT, "evidence": [*draft["evidence"], "observation:x"],
               "changes": changes.pop("entries", [])}
    current.update(changes)
    return current


def test_the_first_revision_names_what_it_changes_from_the_draft():
    _, found = _frame()
    draft = weekly_draft.draft(found, STAMP)
    assert draft_change_errors(draft, {**draft, "decision": JUDGMENT}, None) == []
    easy = next(session for session in draft["sessions"] if session["workout"] == "easy")
    shorter = [{**session, "distance_target_km": session["distance_target_km"] - 1}
               if session["id"] == easy["id"] else session for session in draft["sessions"]]
    [missing] = draft_change_errors(draft, _amended(draft, sessions=shorter), None)
    assert f"{easy['id']} differs from the draft" in missing
    entry = {"session_id": easy["id"], "fields": ["distance_target_km"],
             "trigger": "observation:x", "context": "jambes lourdes", "rule": "n2"}
    assert draft_change_errors(draft, _amended(draft, sessions=shorter, entries=[entry]),
                               None) == [], "above the floor, the evidence is enough"


def test_under_the_floor_or_without_a_key_session_a_restriction_is_needed():
    _, found = _frame()
    draft = weekly_draft.draft(found, STAMP)
    easy = next(session for session in draft["sessions"] if session["workout"] == "easy")
    shorter = [{**session, "distance_target_km": 1.0} if session["id"] == easy["id"] else session
               for session in draft["sessions"]]
    entry = {"session_id": easy["id"], "fields": ["distance_target_km"],
             "trigger": "observation:x", "context": "jambes lourdes", "rule": "n2"}
    floor = draft["volume_target_km"]
    [refused] = draft_change_errors(
        draft, _amended(draft, sessions=shorter, entries=[entry],
                        volume_target_km=floor - 5), floor)
    assert "without a typed premise" in refused
    key = next(session for session in draft["sessions"] if session.get("key_position"))
    dropped = [session for session in draft["sessions"] if session["id"] != key["id"]]
    entry = {"session_id": key["id"], "fields": ["removed"], "trigger": "observation:x",
             "context": "douleur", "rule": "n3"}
    [refused] = draft_change_errors(draft, _amended(draft, sessions=dropped, entries=[entry]),
                                    None)
    assert "without a typed premise" in refused


def test_the_gate_keeps_the_draft_with_the_first_revision():
    store, found = _frame()
    draft = weekly_draft.draft(found, STAMP)
    _, current = plan_revision.checked(store, found.week, {**draft, "decision": JUDGMENT},
                                       None, stamp=STAMP, warn=print)
    assert current["draft"]["sessions"] == draft["sessions"]


def test_the_week_of_a_first_time_carries_it_on_its_long_run():
    store = _lived()
    [first] = [key for key in store.cycle.trame.keys if key.role == "first"]
    _, found = _frame(first.week)
    assert found.firsts == ("reconnaissance",)
    drafted = weekly_draft.draft(found, STAMP)
    assert [(session["workout"], session["transition"]) for session in drafted["sessions"]
            if session.get("transition")] == [("long", "reconnaissance")]
