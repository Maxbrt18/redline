"""The next race's skeleton from PostgreSQL facts, its rendering, its bounds on a revision."""

from __future__ import annotations

import datetime as dt

import pytest

from redline.analysis import ledger, plan_engine
from redline.model.errors import ConfigError, InputError

TODAY = dt.date(2030, 11, 27)
GOAL = {"distance_km": 21.1, "terrain": "road", "peak_volume_km": 50,
        "peak_long_run_km": 20, "quality_per_week": 2}


class Store:
    def __init__(self, context=None, rows=()):
        self.context = GOAL if context is None else context
        self.rows = list(rows)
        self.submitted = []

    def deadline_rows(self):
        return [{"deadline_key": "half-2031", "kind": "race", "status": "scheduled",
                 "due_on": dt.date(2031, 4, 6), "item": "Semi", "context": self.context,
                 "fact_owner": "athlete/goals.md", "blocks": None, "completed_on": None,
                 "starts_on": None, "ends_on": None, "athlete_label": "Semi"}]

    def submit(self, kind, payload, *, command_key, turn_id, actor):
        self.submitted.append((kind, payload))


@pytest.fixture
def runs(monkeypatch):
    """Three closed weeks at 30 km, a 14 km long run among them."""
    rows = []
    for week in range(1, 4):
        monday = dt.date(2030, 11, 25) - dt.timedelta(weeks=week)
        rows += [ledger.Row(monday + dt.timedelta(days=1), "easy", km=8, hours=0.8),
                 ledger.Row(monday + dt.timedelta(days=3), "easy", km=8, hours=0.8),
                 ledger.Row(monday + dt.timedelta(days=6), "long", km=14, hours=1.5)]
    monkeypatch.setattr(ledger, "parse_ledger", lambda store: rows)
    return rows


def test_the_athlete_starts_from_the_closed_weeks_and_the_longest_run(runs):
    start = plan_engine.start_of(runs, TODAY)
    assert (start.volume_km, start.long_run_km) == (30, 14)


def test_the_candidates_start_with_the_current_week(runs):
    found, skeletons = plan_engine.candidates(Store(), TODAY)
    assert found.first == dt.date(2030, 11, 25) and found.template.slug == "half-road"
    assert [skeleton.weeks[0].label for skeleton in skeletons] == ["2030-W48"] * 3
    lines = plan_engine.report(Store(), TODAY)
    assert lines[0].startswith("Squelette à rebours — Semi le 06/04/2031 (21.1 km, road)")
    assert any(line.startswith("Candidat « balanced »") for line in lines)
    assert "redline plan-skeleton --choose" in lines[-1]


def test_a_race_without_its_goal_or_template_says_what_is_missing(runs, monkeypatch):
    with pytest.raises(InputError, match="peak_volume_km"):
        plan_engine.frame(Store({"distance_km": 21.1, "terrain": "road"}), TODAY)
    assert plan_engine.chosen(Store({"distance_km": 21.1}), TODAY) is None
    monkeypatch.setattr(plan_engine.race_model, "template_of", lambda goal: None)
    with pytest.raises(ConfigError, match=r"no plan template serves 21\.1 km on road"):
        plan_engine.frame(Store(), TODAY)


def test_the_chosen_skeleton_is_computed_again_from_today(runs):
    chosen = {**GOAL, "skeleton": {"candidate": "balanced", "specific_weeks": 6,
                                   "deload_every": 4, "second_quality": "specific"}}
    skeleton = plan_engine.chosen(Store(chosen), TODAY)
    assert skeleton.candidate == "balanced" and skeleton.levers.specific_weeks == 6
    later = plan_engine.chosen(Store(chosen), dt.date(2031, 1, 7))
    assert later.weeks[0].label == "2031-W02", "re-planned from where the athlete stands"
    lines = plan_engine.report(Store(chosen), TODAY)
    assert any(line.startswith("Squelette choisi : « balanced »") for line in lines)


def _run(monkeypatch, store, *args):
    from redline.cli import plan_skeleton

    monkeypatch.setattr("redline.storage.open_store", lambda actor: store)
    monkeypatch.setattr("sys.argv", ["plan-skeleton", *args, "--date", TODAY.isoformat()])
    return plan_skeleton.main()


def test_choosing_a_candidate_keeps_its_levers_in_the_races_context(runs, monkeypatch, capsys):
    store = Store()
    assert _run(monkeypatch, store) == 0
    assert "Candidat « cautious »" in capsys.readouterr().out
    assert _run(monkeypatch, store, "--choose", "specific") == 0
    [(kind, payload)] = store.submitted
    assert kind == "deadline.upsert" and payload["deadline_key"] == "half-2031"
    assert payload["context"]["skeleton"] == {"candidate": "specific", "specific_weeks": 6,
                                              "deload_every": 4,
                                              "second_quality": "development"}
    assert payload["context"]["peak_volume_km"] == 50, "the rest of the goal is kept"
    with pytest.raises(InputError, match="candidat inconnu"):
        _run(monkeypatch, store, "--choose", "bold")
