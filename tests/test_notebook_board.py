"""The notebook dashboard, computed by a real PostgreSQL on a typical week.

The week mimics a build week: rest, easy + strength, easy, easy + strength, jog, long run. What
matters is not the rendering but the provenance: intent comes from the revision, the fact from
the ledger, and a notebook answer only writes what the ledger asks for.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest
from conftest import ENGINE_ROOT, reset_database
from test_weekly_plan import revision, session

from redline.model import weekly_plan
from redline.model.activity import Activity
from redline.service.notebook_board import AnswerError, Board
from redline.service.plans import PlanStatus
from redline.storage import Database, OperationalStore

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")
PARIS = dt.timezone(dt.timedelta(hours=2))
WEEK = "2026-W41"


@pytest.fixture
def store():
    reset_database()
    database = Database(URL, application_name="pytest")
    yield OperationalStore(database)
    database.dispose()


class Repo:
    """The minimum of VaultRepo that the notebook reads: status and active revision."""

    def __init__(self, store, status=PlanStatus.VALID):
        self.store, self.status = store, status

    def validate_weekly_plan(self, week):
        return self.status if self.store.load_plan(week) else PlanStatus.INVALID

    def weekly_plan_context(self, week):
        plan = self.store.load_plan(week)
        return weekly_plan.as_json(plan) if plan else ""


def ef(identifier, date, **overrides):
    values = {"distance_target_km": None, "distance_cap_km": None, "duration_cap_min": 55,
              "hr_zone": "Z1-Z2, 130-157 bpm", "hr_cap_bpm": 157}
    values.update(overrides)
    payload = session(identifier, date=date, title="EF, permis accompagné", **values)
    return {key: value for key, value in payload.items() if value is not None}


def seed_week(store):
    sessions = [
        session("w41-lun-repos", date="2026-10-05", workout="rest", title="Repos"),
        ef("w41-mar-ef", "2026-10-06"),
        session("w41-mar-renfo", date="2026-10-06", workout="strength", routine="a",
                title="Renfo, séance unique"),
        ef("w41-mer-ef", "2026-10-07", duration_cap_min=45),
        ef("w41-ven-ef", "2026-10-09"),
        session("w41-sam-footing", date="2026-10-10", title="Footing court",
                distance_target_km=4.5, distance_cap_km=5.0),
        session("w41-dim-sl", date="2026-10-11", workout="long", start_time="09:45",
                title="Sortie longue au Testathlon", location_label="Vincennes",
                distance_target_km=16.0, distance_cap_km=16.5),
    ]
    for item in sessions:
        if item["workout"] == "rest":
            item["kind"] = "rest"
            for key in ("strength", "distance_target_km", "distance_cap_km", "rpe_min", "rpe_max"):
                item.pop(key, None)
    store.append_plan_revision(WEEK, revision(
        sessions=sessions, computed_at="2026-10-04T20:00:00+02:00", volume_target_km=41.0))
    store.add_observation({
        "occurred_on": dt.date(2026, 10, 6), "kind": "easy",
        "started_at": dt.datetime(2026, 10, 6, 7, 10, tzinfo=PARIS),
        "idempotency_key": "strava:90000000018", "strava_activity_id": 90000000018,
        "measurements": {
            "distance_km": {"state": "known", "value": 8.35},
            "duration_s": {"state": "known", "value": 2880},
            "hr_avg_bpm": {"state": "known", "value": 162},
            "rpe": {"state": "known", "value": 3},
            "enjoyment": {"state": "known", "value": 8},
            "elevation_gain_m": {"state": "known", "value": 20},
            "elevation_loss_m": {"state": "known", "value": 20}},
        "pains": {"knee": {"state": "known", "value": 0.5}},
    })
    store.add_observation({
        "occurred_on": dt.date(2026, 10, 7), "kind": "easy",
        "started_at": dt.datetime(2026, 10, 7, 18, 40, tzinfo=PARIS),
        "idempotency_key": "strava:90000000019", "strava_activity_id": 90000000019,
        "measurements": {
            "distance_km": {"state": "known", "value": 5.71},
            "hr_avg_bpm": {"state": "known", "value": 157},
            "rpe": {"state": "pending"},
            "enjoyment": {"state": "pending"},
            "elevation_gain_m": {"state": "known", "value": 7},
            "elevation_loss_m": {"state": "known", "value": 7}},
        "pains": {"knee": {"state": "pending"}},
    })


def board(store, when=dt.datetime(2026, 10, 7, 20, 0, tzinfo=PARIS)):
    return Board(store, Repo(store), ENGINE_ROOT, when.date(), when)


def test_today_puts_the_fact_next_to_the_typed_intention(store):
    seed_week(store)
    view = board(store).today_view()
    [today] = view["sessions"]
    assert today["title"] == "EF, permis accompagné" and today["status"] == "done"
    assert today["fact"]["km"] == 5.71 and today["fact"]["over"] == []
    assert view["tomorrow"]["sessions"] == []
    assert [item["id"] for item in view["tomorrow"]["next"]] == ["w41-ven-ef"]
    assert view["totals"]["km_done"] == pytest.approx(14.06)
    assert view["totals"]["km_target"] == 41.0
    assert view["totals"]["runs_done"] == 2 and view["totals"]["runs_planned"] == 5
    strip = {item["date"]: item for item in view["strip"]}
    assert strip["2026-10-06"]["done_km"] == 8.35 and strip["2026-10-06"]["abbr"] == "EF+R"
    assert strip["2026-10-09"]["planned_label"] == "≤55′"
    assert strip["2026-10-11"]["planned_km"] == 16.0 and strip["2026-10-11"]["long"]
    assert strip["2026-10-05"]["rest"] and strip["2026-10-07"]["today"]


def test_a_crossed_typed_cap_is_named_never_inferred_from_prose(store):
    seed_week(store)
    week = board(store).week_view(WEEK, [])
    tuesday = next(item for item in week["sessions"] if item["id"] == "w41-mar-ef")
    assert tuesday["fact"]["hr_avg"] == 162
    assert tuesday["fact"]["over"] == ["hr_avg"]
    assert tuesday["planned"][:2] == ["≤55 min", "RPE 2-3"]
    assert week["review"] is None and week["plan"]["revision"] == 1


def test_the_coach_waits_only_for_what_the_ledger_claims(store):
    seed_week(store)
    asks = board(store).asks()
    [cells] = [ask for ask in asks if ask["type"] == "cells"]
    assert cells["date"] == "2026-10-07"
    assert cells["fields"] == ["pain:knee", "rpe", "enjoyment"]
    result = board(store).answer(cells["id"] + ":rpe", 2)
    assert result["alert"] is None
    with pytest.raises(AnswerError):
        board(store).answer(cells["id"] + ":rpe", 3)
    with pytest.raises(AnswerError):
        board(store).answer(cells["id"] + ":pain:knee", 1.25)
    result = board(store).answer(cells["id"] + ":pain:knee", 2.5)
    assert "au-dessus du plafond" in result["alert"]
    rows = {row["idempotency_key"]: row for row in store.observation_rows()}
    measures = rows["strava:90000000019"]["measurements"]
    assert measures["rpe"] == {"state": "known", "value": 2.0}
    assert rows["strava:90000000019"]["pains"]["knee"] == {"state": "known", "value": 2.5}


def test_a_net_wake_trace_alerts_the_coach_with_the_session_condition(store):
    """The condition lives in the session, the alert triggers it."""
    seed_week(store)
    morning = dt.datetime(2026, 10, 7, 8, 0, tzinfo=PARIS)
    alert = board(store, morning).answer("wake:2026-10-07:knee", "clear")["alert"]
    assert "trace nette" in alert and "condition écrite dans la séance" in alert


def test_a_very_light_wake_trace_is_half_a_point(store):
    seed_week(store)
    morning = dt.datetime(2026, 10, 7, 8, 0, tzinfo=PARIS)
    assert board(store, morning).answer("wake:2026-10-07:knee", "very-light") == {"alert": None}
    [wake] = [row for row in store.observation_rows() if row["kind"] == "wake-trace"]
    assert wake["pains"]["knee"] == {"state": "known", "value": 0.5}


def test_the_morning_trace_is_asked_in_the_morning_only_and_in_its_own_words(store):
    seed_week(store)
    morning = dt.datetime(2026, 10, 7, 8, 0, tzinfo=PARIS)
    [wake] = [ask for ask in board(store, morning).asks() if ask["type"] == "wake"]
    assert (wake["id"], wake["after_effort"]) == ("wake:2026-10-07:knee", True)
    afternoon = dt.datetime(2026, 10, 7, 15, 0, tzinfo=PARIS)
    assert not [ask for ask in board(store, afternoon).asks() if ask["type"] == "wake"]
    with pytest.raises(AnswerError):
        board(store, morning).answer(wake["id"], 1)
    store.upsert_prediction({
        "prediction_key": "w41-sl-reveil", "domain": "corps",
        "statement": "trace au plus légère au réveil du lendemain de SL",
        "probability": .75, "due_on": dt.date(2026, 10, 11),
        "resolves_on": {"kind": "wake-trace", "date": "2026-10-07", "success_if": "<=1"}})
    assert board(store, morning).answer(wake["id"], "light") == {"alert": None}
    [prediction] = store.prediction_rows()
    assert (prediction["status"], prediction["outcome"]) == ("resolved", True)
    wake = [row for row in store.observation_rows() if row["kind"] == "wake-trace"]
    assert wake[0]["pains"]["knee"] == {"state": "known", "value": 1.0}
    assert wake[0]["source"] == "notebook"
    assert not [ask for ask in board(store, morning).asks() if ask["type"] == "wake"]


def test_the_morning_trace_is_asked_after_a_rest_day_too(store):
    seed_week(store)
    morning = dt.datetime(2026, 10, 6, 8, 0, tzinfo=PARIS)
    [wake] = [ask for ask in board(store, morning).asks() if ask["type"] == "wake"]
    assert (wake["id"], wake["after_effort"]) == ("wake:2026-10-06:knee", False)


def test_the_race_and_only_alex_s_deadlines_reach_the_course_tab(store):
    seed_week(store)
    store.upsert_deadline({
        "deadline_key": "race-targetrace-2026", "due_on": dt.date(2026, 11, 26),
        "item": "TargetRace 45 km — course objectif", "fact_owner": "coaching/race.md",
        "status": "scheduled", "kind": "race", "athlete_label": "TargetRace",
        "context": {"start_time": "09:00", "distance_km": 42.2, "target": "<4 h 30"}})
    store.upsert_deadline({
        "deadline_key": "frontale", "due_on": dt.date(2026, 10, 14),
        "item": "Frontale achetée avant la première sortie de nuit, 400 lumens réels",
        "fact_owner": "coaching/race.md", "status": "todo",
        "athlete_label": "Frontale ≥400 lm, 5 h d’autonomie"})
    store.upsert_deadline({
        "deadline_key": "revue-prediction", "due_on": dt.date(2026, 10, 11),
        "item": "Revue de la prédiction du 05/08", "fact_owner": "coaching/decisions.md",
        "status": "todo"})
    store.evaluate_capability({
        "capability_number": 5, "slug": "cap5-knee", "need": "Genou intact",
        "score": 3, "scale": 5, "status": "amber", "evaluated_on": dt.date(2026, 10, 4)})
    store.evaluate_capability({
        "capability_number": 3, "slug": "cap3-fueling", "need": "Digérer",
        "score": 0, "scale": 5, "status": "red", "evaluated_on": dt.date(2026, 10, 4)})
    archives = [
        {"id": "a", "kind": "monthly", "period": "2026-09", "origin": "recovered"},
        {"id": "b", "kind": "monthly", "period": "2026-09", "origin": "delivery"},
        *({"id": f"w{n}", "kind": "weekly", "period": f"2026-W{n}", "origin": "delivery"}
          for n in (37, 38, 39, 40))]
    view = board(store).race_view(archives)
    assert [entry["id"] for entry in view["archives"]] == ["b", "w40", "w39", "w38"]
    assert view["race"]["days"] == 50 and view["race"]["name"] == "TargetRace"
    assert view["race"]["target"] == "<4 h 30"
    labels = [item["label"] for item in view["milestones"]]
    assert labels == ["Sortie longue au Testathlon", "Frontale ≥400 lm, 5 h d’autonomie", "Départ"]
    assert [item["label"] for item in view["capabilities"]] == ["Knee", "Fueling"]
    assert view["longest"]["km"] == 8.35 and view["longest"]["planned_km"] == 16.0
    assert view["longest"]["planned_on"] == "2026-10-11"
    current = view["longest"]["weeks"][-1]
    assert current["open"] and current["target"] == 41.0 and current["volume"] >= current["km"] == 8.35
    assert board(store).today_view()["race"]["days"] == 50


def test_past_weeks_list_the_ledger_fact_and_the_planned_target(store):
    seed_week(store)
    store.append_plan_revision("2026-W40", revision(
        sessions=[ef("w40-mar-ef", "2026-09-29")], computed_at="2026-09-27T20:00:00+02:00",
        volume_target_km=40.0))
    store.add_observation({
        "occurred_on": dt.date(2026, 9, 29), "kind": "easy", "idempotency_key": "manual:w40",
        "measurements": {"distance_km": {"state": "known", "value": 9.5}}})
    [w40] = board(store).weeks_view({"2026-W40": "bilan-40"})
    assert w40 == {"week": "2026-W40", "monday": "2026-09-28", "km_done": 9.5,
                   "km_target": 40.0, "review": "bilan-40"}


def test_without_a_plan_nothing_is_prescribed_but_the_ledger_still_counts(store):
    store.add_observation({
        "occurred_on": dt.date(2026, 10, 6), "kind": "easy",
        "idempotency_key": "manual:ef", "measurements": {
            "distance_km": {"state": "known", "value": 8.0},
            "rpe": {"state": "known", "value": 3},
            "enjoyment": {"state": "known", "value": 8}},
        "pains": {"knee": {"state": "known", "value": 0}}})
    view = board(store).today_view()
    assert view["plan"]["status"] == "invalid" and view["sessions"] == []
    assert view["totals"]["km_done"] == 8.0 and view["totals"]["km_target"] is None
    week = board(store).week_view(WEEK, [])
    assert [item["km"] for item in week["extra"]] == [8.0]
    json.dumps(view)  # the notebook serves it as is


def test_routes_serve_the_three_screens_and_hand_a_crossing_to_the_coach(
        store, make_config, monkeypatch):
    from unittest.mock import MagicMock

    from conftest import RecordingBot

    from redline.service import clock
    from redline.service.notebook import NotebookService
    from redline.service.web import create_app
    from redline.storage.notebook import NotebookStore

    seed_week(store)
    evening = dt.datetime(2026, 10, 7, 20, 0, tzinfo=PARIS)
    monkeypatch.setattr(clock, "now", lambda: evening)
    monkeypatch.setattr(clock, "today", lambda: evening.date())
    config = make_config()
    archive = NotebookStore(store.database)
    service = NotebookService(archive, Repo(store), MagicMock(), ENGINE_ROOT,
                              "https://coach.test", operational=store)
    monkeypatch.setattr(service, "current", lambda: {"status": "none"})
    bot = RecordingBot()
    client = create_app(config, bot, MagicMock(), notebook=service).test_client()
    for path in ("today", f"week/{WEEK}", "race"):
        assert client.get("/notebook-api/" + path).status_code == 401
    assert client.post("/notebook-api/connect", json={
        "token": archive.issue(config.allowed_psid)}).status_code == 200
    today = client.get("/notebook-api/today").json
    assert today["totals"]["km_target"] == 41.0 and today["strength"] == {"status": "none"}
    assert client.get(f"/notebook-api/week/{WEEK}").json["week"] == WEEK
    assert client.get("/notebook-api/week/2026-W99").status_code == 400
    assert client.get("/notebook-api/race").json["race"] is None
    assert client.get("/notebook-api/weeks").json == {"weeks": []}
    [ask] = today["asks"]
    assert client.post("/notebook-api/answer", json={"ask": "drop table", "value": 1}).status_code == 400
    assert client.post("/notebook-api/answer", json={"ask": ask["id"] + ":pain:knee", "value": 1},
                       headers={"Origin": "https://evil.test"}).status_code == 403
    response = client.post("/notebook-api/answer", json={"ask": ask["id"] + ":pain:knee", "value": 3})
    assert response.json == {"status": "ok", "alert": True}
    assert bot.inbound and "3/10" in bot.inbound[0][1]
    again = client.post("/notebook-api/answer", json={"ask": ask["id"] + ":pain:knee", "value": 1})
    assert again.status_code == 409
    calm = client.post("/notebook-api/answer", json={"ask": ask["id"] + ":rpe", "value": 2})
    assert calm.json == {"status": "ok", "alert": False} and len(bot.inbound) == 1


def test_the_last_feeling_of_a_strava_run_brings_its_debrief_forward(
        store, make_config, monkeypatch):
    """Complete feelings: the athlete finished with the run, the debrief no longer waits."""
    from unittest.mock import MagicMock

    from redline.service import clock
    from redline.service.notebook import NotebookService
    from redline.service.web import create_app
    from redline.storage.notebook import NotebookStore

    seed_week(store)
    store.ingest_strava_activity(Activity(
        id=90000000019, sport="Run", name="Morning Run",
        start_local="2026-10-07T18:40:00", distance_m=5710.0))
    evening = dt.datetime(2026, 10, 7, 20, 0, tzinfo=PARIS)
    monkeypatch.setattr(clock, "now", lambda: evening)
    monkeypatch.setattr(clock, "today", lambda: evening.date())
    config = make_config()
    archive = NotebookStore(store.database)
    service = NotebookService(archive, Repo(store), MagicMock(), ENGINE_ROOT,
                              "https://coach.test", operational=store)
    state, nudge = MagicMock(), MagicMock()
    client = create_app(config, MagicMock(), state, notebook=service,
                        nudge=nudge).test_client()
    client.post("/notebook-api/connect", json={"token": archive.issue(config.allowed_psid)})
    [ask] = client.get("/notebook-api/today").json["asks"]
    for metric, value in (("pain:knee", 1), ("rpe", 3)):
        client.post("/notebook-api/answer", json={"ask": f"{ask['id']}:{metric}", "value": value})
    state.proactive.hasten_job.assert_not_called()
    client.post("/notebook-api/answer", json={"ask": ask["id"] + ":enjoyment", "value": 7})
    state.proactive.hasten_job.assert_called_once()
    assert state.proactive.hasten_job.call_args.args[0] == "strava:90000000019"
    nudge.assert_called_once()
    observation_id = ask["id"].split(":", 1)[1]
    assert store.observation_feelings(observation_id) == {
        "rpe": 3.0, "enjoyment": 7.0, "pains": {"knee": 1.0}}
    assert store.strava_activity_for_observation(observation_id) == 90000000019



def test_the_current_week_announces_the_next_one_once_planned(store):
    """On Sunday the following week is organised: it shows as soon as it exists."""
    seed_week(store)
    sunday = dt.datetime(2026, 10, 11, 13, 0, tzinfo=PARIS)
    assert board(store, sunday).week_view(WEEK, [])["next"] is None

    store.append_plan_revision("2026-W42", revision(
        sessions=[ef("w42-mar-ef", "2026-10-13"), ef("w42-jeu-ef", "2026-10-15")],
        computed_at="2026-10-11T12:30:00+02:00", volume_target_km=32.0))
    view = board(store, sunday)
    assert view.week_view(WEEK, [])["next"] == {
        "week": "2026-W42", "monday": "2026-10-12", "km_target": 32.0, "runs_planned": 2}
    assert view.week_view("2026-W42", [])["next"] is None
