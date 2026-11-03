"""Reorganising a week: what the athlete's days allow, ranked, with nothing broken."""

from __future__ import annotations

import datetime as dt
import itertools
import json
import random

import pytest

from redline.analysis import reorganize
from redline.analysis.reorganize import Item, Remark, Rules
from redline.model import week_days
from redline.model.program import Program
from redline.model.schema import SchemaError
from redline.model.vocabulary import SessionPriority

WEEK = "2030-W10"
DATES = week_days.week_dates(WEEK)
MONDAY = DATES[0]


def day(offset: int) -> dt.date:
    return MONDAY + dt.timedelta(days=offset)


def item(identifier: str, workout: str, offset: int, minutes: int = 50, *, effort: float = 3,
         demanding: bool | None = None, priority: SessionPriority | None = None,
         impact: bool = False, gaps: tuple[tuple[str, int], ...] = (),
         held_until: dt.date | None = None) -> Item:
    hard = workout in {"long", "threshold", "hills"} if demanding is None else demanding
    return Item(id=identifier, title=identifier, workout=workout, home=day(offset),
                demanding=hard,
                priority=priority or (SessionPriority.KEY if hard else SessionPriority.SUPPORT),
                minutes=minutes, load=minutes * effort, strength=workout == "strength",
                impact=impact, gaps=gaps, held_until=held_until)


#: Tuesday easy + strength, Thursday threshold, Friday easy + strength, Saturday short
#: easy, Sunday long run: a common week, Monday and Wednesday free.
def week_items() -> list[Item]:
    return [
        item("tue-easy", "easy", 1, 50),
        item("tue-strength", "strength", 1, 30, effort=6, gaps=(("strength", 48),)),
        item("thu-threshold", "threshold", 3, 60, effort=6.5),
        item("fri-easy", "easy", 4, 50),
        item("fri-strength", "strength", 4, 30, effort=6, gaps=(("strength", 48),)),
        item("sat-short", "easy", 5, 35, effort=2, priority=SessionPriority.OPTIONAL),
        item("sun-long", "long", 6, 180, effort=4.5),
    ]


RULES = Rules(runs_per_day=1, sessions_per_day=2, load_margin_pct=25, long_run_weekdays=(6, 7))


def solve(days=(), pins=(), *, today: int = 1, rules: Rules = RULES, items=None,
          rest_days=frozenset()):
    request = week_days.parse({"days": list(days), "pins": list(pins)}, WEEK)
    return reorganize.reorganise(items or week_items(), DATES, day(today), request, rules,
                                 rest_days=rest_days)


def placed(variant) -> dict[str, dt.date]:
    return dict(variant.placement)


def test_without_a_constraint_the_plan_itself_comes_first():
    outcome = solve()
    assert outcome.variants[0].moves == ()
    assert outcome.variants[0].dropped == ()


def test_a_closed_day_empties_and_the_week_shrinks_rather_than_condenses():
    outcome = solve([{"date": str(day(2)), "kind": "off"}, {"date": str(day(3)), "kind": "off"}])
    best = outcome.variants[0]
    assert best.dropped == ("sat-short",)
    assert all(date not in {day(2), day(3)} for date in placed(best).values())


def test_the_lightest_session_of_the_lowest_priority_goes_first():
    items = [*week_items()[:5], item("sat-short", "easy", 5, 35, effort=2), week_items()[6]]
    outcome = solve([{"date": str(day(2)), "kind": "off"}, {"date": str(day(3)), "kind": "off"}],
                    items=items)
    assert outcome.variants[0].dropped == ("sat-short",)


def test_a_key_session_is_never_dropped():
    closed = [{"date": str(day(offset)), "kind": "off"} for offset in (1, 2, 4, 5)]
    outcome = solve(closed)
    for variant in outcome.variants:
        assert not {"thu-threshold", "sun-long"} & set(variant.dropped)


def test_an_easy_only_day_refuses_a_demanding_session():
    outcome = solve([{"date": str(day(3)), "kind": "easy"}])
    assert all(placed(variant)["thu-threshold"] != day(3) for variant in outcome.variants)


def test_a_short_day_takes_only_what_fits_in_its_minutes():
    outcome = solve([{"date": str(day(1)), "kind": "short", "minutes": 40}])
    for variant in outcome.variants:
        on_tuesday = [name for name, date in placed(variant).items() if date == day(1)]
        assert sum(next(i.minutes for i in week_items() if i.id == name) for name in on_tuesday) <= 40


def test_a_group_carries_the_easy_run_as_long_as_it():
    group = {"date": str(day(5)), "kind": "with_others", "minutes": 60, "pace": "easy",
             "required": True}
    best = solve([group]).variants[0]
    assert best.carried in {"tue-easy", "fri-easy"}
    assert placed(best)[best.carried] == day(5)


def test_a_long_run_wraps_around_a_shorter_group():
    group = {"date": str(day(6)), "kind": "with_others", "minutes": 60, "pace": "easy",
             "required": True}
    best = solve([group]).variants[0]
    assert best.carried == "sun-long"
    assert best.around_min == 120


def test_an_easy_group_never_carries_a_quality_session():
    group = {"date": str(day(3)), "kind": "with_others", "minutes": 60, "pace": "easy",
             "required": True}
    for variant in solve([group]).variants:
        assert variant.carried != "thu-threshold"


def test_a_group_left_uncarried_is_said_when_it_was_optional():
    group = {"date": str(day(3)), "kind": "with_others", "minutes": 120, "pace": "easy",
             "required": False}
    notes = [note.remark for note in solve([group]).variants[0].notes]
    assert Remark.GROUP_UNCARRIED in notes


def test_a_demanding_other_sport_counts_as_a_hard_day():
    sport = {"date": str(day(4)), "kind": "other_sport", "sport": "foot", "demanding": True,
             "run_too": False}
    for variant in solve([sport]).variants:
        at = placed(variant)
        assert at["thu-threshold"] not in {day(3), day(5)}
        assert at["sun-long"] != day(5)


def test_a_pain_signal_lets_a_hard_session_move_later_never_earlier():
    closed = [{"date": str(day(3)), "kind": "off"}]
    calm = solve(closed)
    assert any(placed(variant)["thu-threshold"] < day(3) for variant in calm.variants)
    signal = solve(closed, rules=Rules(**{**vars(RULES), "pain_signal": True}))
    assert all(placed(variant)["thu-threshold"] > day(3) for variant in signal.variants)
    assert signal.held_back == ("thu-threshold",)


def test_a_held_session_does_not_go_before_its_gate():
    items = [*(i for i in week_items() if i.id != "thu-threshold"),
             item("thu-threshold", "threshold", 3, 60, effort=6.5, held_until=day(3))]
    outcome = solve([{"date": str(day(3)), "kind": "off"}], items=items)
    assert all(placed(variant)["thu-threshold"] > day(3) for variant in outcome.variants)


def test_an_impact_routine_next_to_a_hard_run_is_said():
    items = [*(i for i in week_items() if i.id != "fri-strength"),
             item("fri-strength", "strength", 4, 30, effort=6, impact=True,
                  gaps=(("strength", 48),))]
    pins = [{"session": "fri-strength", "date": str(day(5))}]
    notes = solve(pins=pins, items=items).variants[0].notes
    assert Remark.IMPACT_NEAR_HARD in [note.remark for note in notes]


def test_a_week_denser_than_the_plan_says_so():
    pins = [{"session": "sun-long", "date": str(day(5))}]
    outcome = solve(pins=pins, rules=Rules(**{**vars(RULES), "load_margin_pct": 5}))
    notes = [note for variant in outcome.variants for note in variant.notes
             if note.remark is Remark.LOAD_ABOVE_PLAN]
    assert notes and all(note.value > 5 for note in notes)


def test_the_long_run_keeps_its_weekdays_unless_pinned_elsewhere():
    closed = [{"date": str(day(offset)), "kind": "off"} for offset in (5, 6)]
    for variant in solve(closed).variants:
        assert Remark.LONG_OFF_DAYS in [note.remark for note in variant.notes]
    pinned = solve(pins=[{"session": "sun-long", "date": str(day(2))}])
    assert Remark.LONG_OFF_DAYS not in [note.remark for note in pinned.variants[0].notes]
    assert placed(pinned.variants[0])["sun-long"] == day(2)


def test_a_session_on_a_planned_rest_day_is_said():
    pins = [{"session": "thu-threshold", "date": str(day(2))}]
    notes = solve(pins=pins, rest_days=frozenset({day(2)})).variants[0].notes
    assert Remark.REST_DAY_USED in [note.remark for note in notes]


def test_swapping_two_identical_sessions_is_one_variant():
    outcome = solve([{"date": str(day(3)), "kind": "off"}])
    signatures = [tuple(sorted((date, name.split("-")[1]) for name, date in variant.placement))
                  for variant in outcome.variants]
    assert len(signatures) == len(set(signatures))


def test_the_same_request_gives_the_same_variants():
    days = [{"date": str(day(3)), "kind": "off"}]
    assert solve(days) == solve(days)


def test_nothing_before_today_moves_and_today_still_does():
    outcome = solve([{"date": str(day(3)), "kind": "off"}], today=3)
    for variant in outcome.variants:
        assert all(date >= day(3) for date in placed(variant).values())


@pytest.mark.parametrize("seed", range(40))
def test_no_variant_ever_breaks_a_filter(seed):
    rng = random.Random(seed)
    kinds = ["as_planned", "off", "easy", "short", "more", "with_others", "other_sport"]
    days = []
    for offset in rng.sample(range(7), rng.randint(0, 4)):
        kind = rng.choice(kinds)
        entry: dict = {"date": str(day(offset)), "kind": kind}
        if kind == "short":
            entry["minutes"] = rng.choice([30, 45, 60])
        if kind == "with_others":
            entry.update(minutes=rng.choice([45, 60, 90]), pace="easy", required=rng.random() < .5)
        if kind == "other_sport":
            entry.update(sport="x", demanding=rng.random() < .5, run_too=False)
        days.append(entry)
    rules = Rules(**{**vars(RULES), "pain_signal": rng.random() < .3})
    today = rng.randint(0, 3)
    outcome = solve(days, today=today, rules=rules)
    by_id = {i.id: i for i in week_items()}
    states = {dt.date.fromisoformat(entry["date"]): entry for entry in days}
    for variant in outcome.variants:
        at = placed(variant)
        assert not {"thu-threshold", "sun-long"} & set(variant.dropped)
        hard = sorted({date for name, date in at.items() if by_id[name].demanding}
                      | {d for d, e in states.items() if e.get("demanding")})
        assert all((b - a).days > 1 for a, b in itertools.pairwise(hard))
        for name, date in at.items():
            assert date >= day(today)
            state = states.get(date, {})
            assert state.get("kind") not in {"off", "other_sport"}
            if state.get("kind") == "easy":
                assert not by_id[name].demanding
            if rules.pain_signal and by_id[name].demanding:
                assert date >= by_id[name].home
        runs = [date for name, date in at.items() if not by_id[name].strength]
        assert len(runs) == len(set(runs))
        strength = sorted(date for name, date in at.items() if by_id[name].strength)
        assert all((b - a).days >= 2 for a, b in itertools.pairwise(strength))
        for date, entry in states.items():
            if entry["kind"] == "with_others" and entry["required"]:
                assert variant.carried is not None and at[variant.carried] == date


# ------------------------------------------------------------------ the plan's sessions

def test_items_read_priority_minutes_and_gates_from_the_plan():
    sessions = [
        {"id": "a", "title": "EF", "date": "2030-03-05", "kind": "run", "workout": "easy",
         "status": "planned", "demanding": False, "duration_cap_min": 45, "rpe_min": 2,
         "rpe_max": 3, "priority": "optional", "sources": ["x"]},
        {"id": "b", "title": "Seuil", "date": "2030-03-07", "kind": "run",
         "workout": "threshold", "status": "planned", "demanding": True,
         "duration_cap_min": 60, "rpe_min": 6, "rpe_max": 7, "sources": ["x"]},
        {"id": "c", "title": "Renfo", "date": "2030-03-05", "kind": "strength",
         "workout": "strength", "routine": "a", "status": "planned", "demanding": False,
         "strength": {"drop": ["jumps"]}, "sources": ["x"]},
        {"id": "d", "title": "Repos", "date": "2030-03-04", "kind": "rest", "workout": "rest",
         "status": "planned", "demanding": False, "sources": ["x"]},
    ]
    gates = [{"id": "g", "due": "2030-03-07", "when": "before", "holds": ["b"],
              "question": "pas de trace ce matin ?", "on_fail": "EF"}]
    program = Program({"jumps": {"impact": True}, "calf": {}},
                      {"a": {"exercises": ["jumps", "calf"], "duration_min": 30,
                             "target_rpe": 6, "min_gap_h": {"strength": 48}}})
    found = {i.id: i for i in reorganize.items(sessions, gates, program, {})}
    assert set(found) == {"a", "b", "c"}
    assert found["a"].priority is SessionPriority.OPTIONAL
    assert found["b"].priority is SessionPriority.KEY
    assert found["b"].held_until == dt.date(2030, 3, 7)
    assert found["c"].minutes == 30 and found["c"].load == 180
    assert found["c"].impact is False
    assert found["c"].gaps == (("strength", 48),)


def test_a_routine_jumps_until_its_jumps_are_dropped():
    program = Program({"jumps": {"impact": True}, "calf": {}},
                      {"a": {"exercises": ["jumps", {"exercise": "calf", "dose": {}}]}})
    assert program.impact("a")
    assert not program.impact("a", ("jumps",))


# ------------------------------------------------------------------ the request

def test_a_request_is_read_once_and_kept_inside_its_week():
    request = week_days.parse({"days": [
        {"date": "2030-03-06", "kind": "other_sport", "sport": "volley", "demanding": False,
         "run_too": False},
        {"date": "2030-03-09", "kind": "with_others", "minutes": 60, "pace": "easy",
         "required": False}],
        "pins": [{"session": "sun-long", "date": "2030-03-09"}]}, WEEK)
    assert request.day(dt.date(2030, 3, 6)).closed
    assert request.day(dt.date(2030, 3, 5)).kind.value == "as_planned"
    assert request.pins == {"sun-long": dt.date(2030, 3, 9)}


@pytest.mark.parametrize("days", [
    [{"date": "2030-03-11", "kind": "off"}],
    [{"date": "2030-03-06", "kind": "off"}, {"date": "2030-03-06", "kind": "easy"}],
    [{"date": "2030-03-06", "kind": "off", "minutes": 30}],
    [{"date": "2030-03-06", "kind": "short"}],
    [{"date": "2030-03-06", "kind": "with_others", "minutes": 60}],
])
def test_a_request_that_does_not_hold_together_is_refused(days):
    with pytest.raises(SchemaError):
        week_days.parse({"days": days}, WEEK)


def test_the_outcome_reads_as_json():
    data = reorganize.as_dict(solve([{"date": str(day(2)), "kind": "off"},
                                     {"date": str(day(3)), "kind": "off"}]))
    assert json.loads(json.dumps(data)) == data
    assert data["variants"][0]["dropped"] == ["sat-short"]


# ------------------------------------------------------------------ the command

def _plan() -> dict:
    def run(identifier, date, workout, minutes, **extra):
        return {"id": identifier, "title": extra.pop("title", identifier), "date": date,
                "kind": "run", "workout": workout, "status": "planned",
                "demanding": workout in {"long", "threshold"}, "duration_cap_min": minutes,
                "rpe_min": 2, "rpe_max": 3, "sources": ["[[current-plan]]"], **extra}
    sessions = [run("w10-tue-ef", "2030-03-05", "easy", 50, title="EF"),
                run("w10-thu-seuil", "2030-03-07", "threshold", 60, title="Seuil"),
                run("w10-sat-foot", "2030-03-09", "easy", 35, title="Footing",
                    priority="optional"),
                run("w10-sun-sl", "2030-03-10", "long", 180, title="Sortie longue")]
    return {"week": WEEK, "revisions": [{
        "revision": 1, "computed_at": "2030-03-03T18:00:00+01:00", "reason": "bilan",
        "evidence": ["registre"], "constraints": [], "sessions": sessions}]}


class _Store:
    def load_plan(self, week):
        return _plan()

    def habits(self, on=None, until=None):
        return []

    def stated_days(self, first, last):
        return []


def _run_variants(monkeypatch, tmp_path, days, as_json):
    from conftest import load_script

    week_plan = load_script("week-plan")
    monkeypatch.setattr("redline.service.reorganize.pain_signal", lambda store, today: False)
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    request = tmp_path / "days.json"
    request.write_text(json.dumps({"days": days}), encoding="utf-8")
    return week_plan._variants(_Store(), WEEK, str(request), as_json, today=dt.date(2030, 3, 5))


def test_the_command_reads_the_variants_back_in_the_coachs_words(monkeypatch, tmp_path, capsys):
    days = [{"date": "2030-03-07", "kind": "off"}, {"date": "2030-03-06", "kind": "off"},
            {"date": "2030-03-08", "kind": "off"}]
    assert _run_variants(monkeypatch, tmp_path, days, as_json=False) == 0
    out = capsys.readouterr().out
    assert "Variante 1" in out
    assert "Footing : saute" in out
    assert "Seuil" in out


def test_the_command_prints_json_for_the_notebook(monkeypatch, tmp_path, capsys):
    days = [{"date": "2030-03-09", "kind": "with_others", "minutes": 50, "pace": "easy",
             "required": True}]
    assert _run_variants(monkeypatch, tmp_path, days, as_json=True) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["week"] == WEEK and data["revision"] == 1
    assert data["variants"][0]["carried"] == "w10-tue-ef"


# ------------------------------------------------------------------ taking a variant

def _database():
    import os

    from conftest import reset_database

    from redline.storage import Database, OperationalStore

    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL absent")
    reset_database()
    database = Database(url, application_name="pytest")
    return database, OperationalStore(database)


@pytest.fixture
def seeded(monkeypatch):
    database, store = _database()
    plan = _plan()
    first = dict(plan["revisions"][0], volume_target_km=40.0,
                 decision={"by": "request", "alternatives": []})
    store.append_plan_revision(WEEK, first, actor="test")
    monkeypatch.setattr("redline.service.reorganize.pain_signal", lambda store, today: False)
    yield store
    database.dispose()


THURSDAY_AND_FRIDAY_OFF = {"days": [{"date": "2030-03-06", "kind": "off"},
                                    {"date": "2030-03-07", "kind": "off"},
                                    {"date": "2030-03-08", "kind": "off"}]}


def test_taking_a_variant_writes_the_next_revision_with_its_evidence(seeded):
    from redline.service import reorganize as service

    number = service.take(seeded, WEEK, THURSDAY_AND_FRIDAY_OFF, expected=1, choice=0,
                          today=dt.date(2030, 3, 5), stamp="2030-03-05T12:00:00+01:00")
    assert number == 2
    revision = seeded.load_plan(WEEK)["revisions"][-1]
    sessions = {session["id"]: session for session in revision["sessions"]}
    assert sessions["w10-sat-foot"]["status"] == "cancelled"
    assert sessions["w10-thu-seuil"]["date"] != "2030-03-07"
    assert revision["evidence"][0].startswith("availability: carnet")
    assert revision["decision"]["by"] == "request"
    assert revision["volume_target_km"] == 40.0
    assert revision["state_snapshot"]["as_of"] == "2030-03-05"


def test_a_variant_is_refused_once_the_week_has_moved_on(seeded):
    from redline.service import reorganize as service

    with pytest.raises(service.ReorganizeError):
        service.take(seeded, WEEK, THURSDAY_AND_FRIDAY_OFF, expected=7, choice=0,
                     today=dt.date(2030, 3, 5), stamp="2030-03-05T12:00:00+01:00")
    with pytest.raises(service.ReorganizeError):
        service.take(seeded, WEEK, THURSDAY_AND_FRIDAY_OFF, expected=1, choice=9,
                     today=dt.date(2030, 3, 5), stamp="2030-03-05T12:00:00+01:00")
    with pytest.raises(service.ReorganizeError):
        service.take(seeded, WEEK, {"days": []}, expected=1, choice=0,
                     today=dt.date(2030, 3, 5), stamp="2030-03-05T12:00:00+01:00")


def test_the_screen_serves_its_declared_shape(seeded):
    import contract

    from redline.model import notebook_views as views
    from redline.service import reorganize as service

    shown = service.view(seeded, WEEK, THURSDAY_AND_FRIDAY_OFF, dt.date(2030, 3, 5))
    assert contract.problems(views.ReorganizeView, shown) == []
    assert shown["blocked"] is None
    assert shown["variants"][0]["dropped"][0]["title"] == "Footing"
    closed = service.view(seeded, WEEK, {"days": []}, dt.date(2030, 3, 12))
    assert closed["blocked"] == "closed" and closed["variants"] == []


# ------------------------------------------------------------------ habits and days stated ahead

def _habit(weekday, kind, starts_on="2030-01-01", ends_on=None, **details):
    return {"id": f"h{weekday}{kind}", "weekday": weekday,
            "starts_on": dt.date.fromisoformat(starts_on),
            "ends_on": dt.date.fromisoformat(ends_on) if ends_on else None, "kind": kind,
            **{name: details.get(name) for name in
               ("minutes", "pace", "required", "sport", "demanding", "run_too")}}


def test_a_day_is_what_was_touched_else_stated_else_its_habit():
    habits = [_habit(3, "other_sport", sport="volley", demanding=False, run_too=False),
              _habit(6, "with_others", minutes=60, pace="easy", required=False),
              _habit(7, "with_others", minutes=90, pace="easy", required=True),
              _habit(1, "off", ends_on="2030-02-01")]
    stated = [{"day": day(5), "kind": "as_planned"}, {"day": day(3), "kind": "short",
                                                       "minutes": 30}]
    touched = week_days.parse({"days": [{"date": str(day(2)), "kind": "easy"}]}, WEEK)
    found = week_days.effective(WEEK, habits, stated, touched).days
    assert found[day(2)].kind.value == "easy" and not found[day(2)].habit
    assert found[day(3)].kind.value == "short"
    assert day(5) not in found and day(0) not in found
    assert found[day(6)].habit and found[day(6)].minutes == 90


def test_the_latest_habit_of_a_weekday_holds():
    older, newer = _habit(6, "off", "2030-01-01"), _habit(6, "easy", "2030-02-01")
    assert week_days.habit_on([older, newer], day(5))["kind"] == "easy"
    assert week_days.habit_on([older, newer], dt.date(2030, 1, 12))["kind"] == "off"


def test_a_habit_must_change_something():
    with pytest.raises(SchemaError):
        week_days.HABIT_SCHEMA.validate({"weekday": 3, "kind": "as_planned"})
    with pytest.raises(SchemaError):
        week_days.HABIT_SCHEMA.validate({"weekday": 8, "kind": "off"})


def test_habits_and_stated_days_are_kept_and_read_back(seeded):
    from redline.service import reorganize as service

    today = dt.date(2030, 3, 5)
    service.add_habit(seeded, {"weekday": 3, "kind": "other_sport", "sport": "volley",
                               "demanding": False, "run_too": False}, today)
    service.add_habit(seeded, {"weekday": 3, "kind": "off"}, today)
    habits = service.habits_view(seeded, today)["habits"]
    assert [habit["kind"] for habit in habits] == ["off"]
    service.state_days(seeded, WEEK, {"days": [{"date": "2030-03-04", "kind": "off"},
                                               {"date": "2030-03-08", "kind": "easy"}]}, today)
    assert [row["day"] for row in seeded.stated_days(DATES[0], DATES[-1])] == [dt.date(2030, 3, 8)]
    readback = service.days_readback(seeded, WEEK)
    assert "pas de course (habitude)" in readback and "facile seulement" in readback
    service.end_habit(seeded, habits[0]["id"], today)
    assert service.habits_view(seeded, today)["habits"] == []


def test_a_habit_steers_the_variants_and_a_week_to_come_is_prepared(seeded):
    from redline.service import reorganize as service

    today = dt.date(2030, 3, 5)
    service.add_habit(seeded, {"weekday": 4, "kind": "off"}, today)
    shown = service.view(seeded, WEEK, {"days": []}, today)
    assert shown["days"][3]["stated"]["kind"] == "off" and shown["days"][3]["habit_id"]
    assert all(variant["days"][3]["labels"] == [] for variant in shown["variants"])
    coming = service.view(seeded, "2030-W11", {"days": []}, today)
    assert coming["written"] is False and coming["blocked"] is None
    assert coming["days"][3]["stated"]["habit"] is True
