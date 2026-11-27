"""The next race's skeleton from PostgreSQL facts, its rendering, its bounds on a revision."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from redline.analysis import ledger, plan_engine
from redline.model.errors import ConfigError, InputError
from redline.model.vocabulary import PlanPhase

TODAY = dt.date(2030, 11, 27)
GOAL = {"distance_km": 21.1, "terrain": "road", "peak_volume_km": 50,
        "peak_long_run_km": 20, "quality_per_week": 2}


class Store:
    def __init__(self, context=None, rows=(), efforts=(), breaks=()):
        self.context = GOAL if context is None else context
        self.rows = list(rows)
        self.efforts, self.breaks = list(efforts), list(breaks)
        self.submitted = []

    def effort_rows(self):
        return self.efforts

    def break_rows(self):
        return self.breaks

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


# ------------------------------------------------------------------ the bounds


def _skeleton(runs_rows):
    chosen = {**GOAL, "skeleton": {"candidate": "balanced", "specific_weeks": 6,
                                   "deload_every": 4, "second_quality": "specific"}}
    return plan_engine.chosen(Store(chosen), TODAY)


def _session(identifier, workout, km=None, status="planned"):
    return {"id": identifier, "workout": workout, "status": status,
            **({"distance_cap_km": km} if km is not None else {})}


def test_a_revision_stays_within_its_weeks_bounds(runs):
    skeleton = _skeleton(runs)
    week = skeleton.week("2030-W48")
    assert week.phase == PlanPhase.BASE and week.qualities == 1
    fine = {"revision": 1, "volume_target_km": week.volume_km,
            "sessions": [_session("sun", "long", week.long_run_km), _session("thu", "threshold")]}
    assert plan_engine.revision_errors(skeleton, "2030-W48", fine, None) == []
    over = {"revision": 1, "volume_target_km": week.volume_km + 5,
            "sessions": [_session("sun", "long", week.long_run_km + 3),
                         _session("tue", "threshold"), _session("thu", "hills")]}
    found = plan_engine.revision_errors(skeleton, "2030-W48", over, None)
    assert len(found) == 4, found
    assert "volume_target_km" in found[0] and "skeleton « balanced »" in found[0]
    assert any("2 quality sessions for 1" in problem for problem in found)
    assert any("hills is not a family of the week" in problem for problem in found)
    assert plan_engine.revision_errors(skeleton, "2031-W30", over, None) == [], \
        "a week outside the skeleton is not bound"


def test_what_the_previous_revision_held_is_carried_not_given_again(runs):
    skeleton = _skeleton(runs)
    week = skeleton.week("2030-W48")
    held = {"revision": 1, "volume_target_km": week.volume_km + 5,
            "sessions": [_session("sun", "long", week.long_run_km + 3)]}
    again = {**held, "revision": 2}
    assert plan_engine.revision_errors(skeleton, "2030-W48", again, held) == []
    more = {**held, "revision": 2, "volume_target_km": week.volume_km + 6}
    assert len(plan_engine.revision_errors(skeleton, "2030-W48", more, held)) == 1


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


def test_what_the_athlete_held_is_read_from_the_closed_weeks(runs):
    from redline.analysis import expected_rpe

    quality = [ledger.Row(dt.date(2030, 11, 25) - dt.timedelta(weeks=week, days=-2), "threshold",
                          km=10, hours=0.9) for week in range(1, 4)]
    rows = [*runs, *quality]
    history = expected_rpe.History(rows, lambda row: None, lambda day: None,
                                   expected_rpe.Settings(26, 3, 5, 1.0, 1.5, 2.0))

    def dose(row):
        intensity = 1.3 if row.type == "threshold" else 1.0
        return expected_rpe.run_dose(row.km or 0.0, 0.0, intensity, 2.0)

    history.dose = dose  # type: ignore[method-assign]
    held = plan_engine.held_of(rows, history, TODAY)
    assert held.quality_premium == pytest.approx(10 * 1.3 ** 2 - 10)
    assert held.week_load == pytest.approx(30 + 10 * 1.3 ** 2)
    assert held.long_share == pytest.approx(14 / 40)
    line = plan_engine._held_line(held)
    assert "une qualité ajoute 6,9 de charge" in line and "35 %" in line


def _effort(key, on, km, seconds, terrain="flat"):
    return {"effort_key": key, "done_on": on, "distance_km": km, "duration_s": seconds,
            "terrain": terrain, "kind": "race", "note": ""}


def test_a_road_goal_reads_its_declared_efforts_with_their_range(runs):
    store = Store(efforts=[_effort("ten", dt.date(2030, 3, 2), 10, 40 * 60)])
    lines = plan_engine.report(store, TODAY)
    found = next(line for line in lines if line.startswith("Estimation du chrono"))
    assert "fourchette" in found and "goal_source = equivalence" in found
    assert any("ne repose que sur un effort" in line for line in lines)
    assert any("course de 10 km en 0:40:00 le 02/03/2030" in line and "part 100 %" in line
               for line in lines)


def test_a_break_after_the_effort_keeps_only_the_slow_half(runs):
    store = Store(efforts=[_effort("ten", dt.date(2030, 3, 2), 10, 40 * 60)],
                  breaks=[{"break_key": "shin", "starts_on": dt.date(2030, 5, 1),
                           "ends_on": dt.date(2030, 6, 15), "note": ""}])
    reading = plan_engine.frame(store, TODAY).reading
    assert reading is not None and reading.estimate is not None
    assert reading.estimate.low_s == reading.estimate.time_s
    lines = plan_engine.report(store, TODAY)
    assert any("arrêt déclaré du 01/05/2030 au 15/06/2030" in line for line in lines)


def test_without_an_effort_the_estimate_says_what_would_give_one(runs):
    store = Store(efforts=[_effort("trail", dt.date(2030, 9, 1), 20, 2 * 3600, "hilly")])
    lines = plan_engine.report(store, TODAY)
    assert any(line.startswith("Estimation du chrono : aucun effort à fond") for line in lines)
    assert any("contrôle : les séances à plat" in line for line in lines)


def test_a_trail_goal_has_no_estimate(runs):
    store = Store(efforts=[_effort("ten", dt.date(2030, 9, 1), 10, 40 * 60)])
    trail = SimpleNamespace(terrain="trail", distance_km=30.0)
    assert plan_engine.goal_reading(store, runs, trail, TODAY) is None


def test_two_test_efforts_give_the_critical_speed_and_its_domains(runs):
    day = dt.date(2030, 11, 10)
    store = Store(efforts=[{**_effort("t1200", day, 1.2, 4 * 60), "kind": "test"},
                           {**_effort("t3600", day, 3.6, 14 * 60), "kind": "test"}])
    lines = plan_engine.report(store, TODAY)
    found = next(line for line in lines if line.startswith("Vitesse critique"))
    assert "sur 2 efforts à fond du 10/11/2030 au 10/11/2030" in found
    assert "aucune erreur d'ajustement" in found
    assert any(line.startswith("  domaines : EF plus lente que") for line in lines)


def test_without_test_efforts_the_coach_may_propose_the_test(runs):
    lines = plan_engine.report(Store(), TODAY)
    assert any(line.startswith("Vitesse critique : aucune") and "1 200 m puis 3 600 m" in line
               for line in lines)
