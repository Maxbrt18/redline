"""The notebook contract, from its three sides.

`redline.model.notebook_views` declares what crosses between Python and JavaScript. The real
routes answer with those shapes, the payloads the browser tests fake are those shapes, and
the page's modules read every declared key. A key renamed on one side fails on the others,
including a page left on the old name: the new key is then read by nothing.
"""

from __future__ import annotations

import datetime as dt
import os
import typing
from unittest.mock import MagicMock

import contract
import pytest
from conftest import TEST_VAULT, reset_database
from notebook_payloads import DETAIL, INJURY, RACE, TODAY, WEEK, WEEKS
from test_notebook_board import PARIS, Repo, ef, seed_week
from test_notebook_board import WEEK as SEEDED_WEEK
from test_page_modules import modules
from test_session_detail import observation_of, seed_threshold, stream
from test_weekly_plan import revision

from redline.model import instance
from redline.model import notebook_views as views
from redline.model.activity import Activity
from redline.service import clock, page, session_detail
from redline.service.notebook import NotebookService
from redline.service.web import create_app
from redline.storage import Database, OperationalStore
from redline.storage.notebook import NotebookStore

URL = os.environ.get("TEST_DATABASE_URL", "")
needs_database = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")
SAM = instance.load(TEST_VAULT)
RACE_DAY = {"name": "TargetRace", "date": "2026-11-26", "days": 50}

#: Declared keys the page has no use for: the service sends them, nothing reads them.
UNREAD = {"created_at", "has_image", "humidity", "revision", "strength_done"}


# ------------------------------------------------------------ the checker
def test_a_declared_object_may_leave_its_optional_keys_out():
    assert contract.problems(views.Race, RACE_DAY) == []


@pytest.mark.parametrize(("change", "problem"), [
    ({"days": None}, "$.days: expected int, got None"),
    ({"days": True}, "$.days: expected int, got True"),
    ({"distance_km": "42"}, "$.distance_km: expected float, got '42'"),
    ({"rank": 3}, "$.rank: not in Race"),
])
def test_a_wrong_or_undeclared_value_is_named_by_its_path(change, problem):
    assert contract.problems(views.Race, RACE_DAY | change) == [problem]


def test_a_missing_key_is_named_by_its_path():
    assert contract.problems(views.Race, {"name": "x", "date": "2026-11-26"}) == ["$.days: missing"]


def test_a_tagged_union_reports_its_closest_shape():
    ask = {"id": "daily:2026-10-07", "type": "daily", "date": "2026-10-07"}
    assert contract.problems(views.Ask, ask) == ["$.fields: missing"]


# ------------------------------------------------------------ the browser fixtures
@pytest.mark.parametrize(("shape", "payload"), [
    (views.TodayView, TODAY), (views.WeekView, WEEK), (list[views.PastWeek], WEEKS["weeks"]),
    (views.InjuryView, INJURY), (views.RaceView, RACE), (views.SessionView, DETAIL),
], ids=["today", "week", "weeks", "injury", "race", "detail"])
def test_a_browser_payload_is_what_the_service_serves(shape, payload):
    assert contract.problems(shape, payload) == []


# ------------------------------------------------------------ the page
def test_the_page_reads_every_declared_key():
    declared = {key for shape in vars(views).values() if typing.is_typeddict(shape)
                for key in shape.__annotations__}
    scripts = [name for name in page.NOTEBOOK_SCRIPT if name.startswith("notebook/")]
    read = {value.strip("'\"") for _, tokens, _, _ in modules(scripts).values()
            for kind, value, _ in tokens if kind in {"id", "str"}}
    assert declared - read == UNREAD


# ------------------------------------------------------------ the routes
@pytest.fixture
def store():
    reset_database()
    database = Database(URL, application_name="pytest")
    yield OperationalStore(database)
    database.dispose()


@pytest.fixture
def connect(store, make_config, monkeypatch):
    """A connected client of the real routes, at a given moment of the seeded weeks."""
    def at(moment: dt.datetime):
        monkeypatch.setattr(clock, "now", lambda: moment)
        monkeypatch.setattr(clock, "today", lambda: moment.date())
        config = make_config()
        archive = NotebookStore(store.database)
        service = NotebookService(archive, Repo(store), MagicMock(), TEST_VAULT,
                                  "https://coach.test", operational=store, instance=SAM)
        client = create_app(config, MagicMock(), MagicMock(), notebook=service).test_client()
        client.post("/notebook-api/connect", json={"token": archive.issue(config.allowed_psid)})
        return client, archive, config.allowed_psid
    return at


def served(client, path: str):
    response = client.get("/notebook-api/" + path)
    assert response.status_code == 200, response.text
    return response.json


def seed_race_tab(store):
    store.upsert_deadline({
        "deadline_key": "race-targetrace-2026", "due_on": dt.date(2026, 11, 26),
        "item": "TargetRace 45 km", "fact_owner": "coaching/race.md", "status": "scheduled",
        "kind": "race", "athlete_label": "TargetRace",
        "context": {"start_time": "09:00", "distance_km": 42.2, "elevation_gain_m": 900,
                    "elevation_loss_m": 900, "elevation_approximate": True, "target": "<4 h 30"}})
    store.upsert_deadline({
        "deadline_key": "frontale", "due_on": dt.date(2026, 10, 14), "item": "Frontale",
        "fact_owner": "coaching/race.md", "status": "todo", "athlete_label": "Frontale"})
    store.evaluate_capability({
        "capability_number": 5, "slug": "cap5-knee", "need": "Genou intact", "score": 3,
        "scale": 5, "status": "amber", "evaluated_on": dt.date(2026, 10, 4)})


def seed_neighbour_weeks(store):
    store.append_plan_revision("2026-W40", revision(
        sessions=[ef("w40-mar-ef", "2026-09-29")], computed_at="2026-09-27T20:00:00+02:00",
        volume_target_km=40.0))
    store.add_observation({
        "occurred_on": dt.date(2026, 9, 29), "kind": "easy", "idempotency_key": "manual:w40",
        "measurements": {"distance_km": {"state": "known", "value": 9.5}}})
    store.append_plan_revision("2026-W42", revision(
        sessions=[ef("w42-mar-ef", "2026-10-13")], computed_at="2026-10-11T20:00:00+02:00",
        volume_target_km=44.0))


def seed_strength_report(store):
    assert store.accept_strength_report({
        "session_key": "w41-renfo", "performed_on": dt.date(2026, 10, 6), "workout": "a",
        "duration_minutes": 30, "note": "", "exercises": [],
        "pains": {"knee": {"before": 1, "after": 0.5}},
        "message_id": "strength:w41-renfo", "psid": "4242", "message": "x"},
        max_per_day=8) == "accepted"


def seed_walk(store):
    store.add_observation({
        "occurred_on": dt.date(2026, 10, 7), "kind": "hike", "idempotency_key": "manual:walk",
        "measurements": {"distance_km": {"state": "known", "value": 3.1}}})


def seed_archives(archive, psid):
    def card(slot, kind, period, day):
        return {"slot": slot, "kind": kind, "title": slot, "period": period, "origin": "recovered",
                "occurred_on": day, "body": ""}
    archive.save_many(psid, "contract:cards", [
        card("review", "weekly", SEEDED_WEEK, dt.date(2026, 10, 11)),
        card("august", "monthly", "2026-09", dt.date(2026, 10, 1)),
        card("strength", "strength", "2026-10-06", dt.date(2026, 10, 6)),
        card("other", "other", "", dt.date(2026, 10, 7))])


@needs_database
def test_the_screens_serve_their_declared_shapes(store, connect):
    seed_week(store)
    seed_race_tab(store)
    seed_neighbour_weeks(store)
    seed_strength_report(store)
    seed_walk(store)
    client, archive, psid = connect(dt.datetime(2026, 10, 7, 8, 0, tzinfo=PARIS))
    seed_archives(archive, psid)
    assert contract.problems(views.TodayView, served(client, "today")) == []
    assert contract.problems(views.WeekView, served(client, f"week/{SEEDED_WEEK}")) == []
    assert contract.problems(list[views.PastWeek], served(client, "weeks")["weeks"]) == []
    assert contract.problems(views.InjuryView, served(client, "injury")) == []
    assert contract.problems(views.RaceView, served(client, "race")) == []


@needs_database
def test_today_serves_a_ready_strength_session_as_declared(store, connect):
    seed_week(store)
    client, _, _ = connect(dt.datetime(2026, 10, 6, 8, 0, tzinfo=PARIS))
    today = served(client, "today")
    assert today["strength"]["status"] == "ready"
    assert contract.problems(views.TodayView, today) == []


def seed_easy_run(store, activity_id, session_id, day):
    store.ingest_strava_activity(Activity(
        id=activity_id, sport="Run", name="EF", start_local=f"{day}T10:00:00", distance_m=9000.0,
        moving_s=3000, elevation_gain_m=120.0, hr_avg_bpm=150.0, hr_max_bpm=165.0,
        cadence_spm=82.0, gear_id="g-shoe"))
    if observation_of(store, activity_id) is None:
        store.match_session(f"2026-W43:{session_id}", activity_id)
    observation_id = observation_of(store, activity_id)
    store.store_strava_detail(activity_id, weather={
        "temp": 18.0, "feels_like": 17.0, "humidity": 60, "wind": 9.0, "wbgt": 15.0,
        "weather_code": 2}, streams=stream(seconds=2400, stop_at=600, stop_s=90, climb=0.05))
    return observation_id


@needs_database
def test_a_quality_session_detail_serves_its_declared_shape(store, connect):
    observation_id = seed_threshold(store)
    store.store_strava_detail(store.strava_detail(observation_id)["activity_id"],
                              streams=stream(seconds=900, hr=170), weather={"temp": 18.0})
    client, _, _ = connect(dt.datetime(2026, 10, 26, 20, 0, tzinfo=PARIS))
    view = served(client, f"session/{observation_id}")
    assert view["reps"] and view["phases"] and view["ask"]
    assert contract.problems(views.SessionView, view) == []


@needs_database
def test_an_easy_session_detail_serves_its_declared_shape(store, connect, monkeypatch):
    store.upsert_gear({"gear_id": "g-shoe", "name": "Trail Shoe 3", "role": "trail",
                       "retire_at_km": 650, "status": "active"})
    store.append_plan_revision("2026-W43", revision(
        sessions=[ef("w43-lun-ef", "2026-10-19"), ef("w43-mar-ef", "2026-10-20")],
        computed_at="2026-10-18T20:00:00+02:00", volume_target_km=30.0))
    seed_easy_run(store, 90000000031, "w43-lun-ef", "2026-10-19")
    observation_id = seed_easy_run(store, 90000000032, "w43-mar-ef", "2026-10-20")
    monkeypatch.setattr(session_detail, "fetch_streams", lambda _: pytest.fail("fetched"))
    client, _, _ = connect(dt.datetime(2026, 10, 21, 20, 0, tzinfo=PARIS))
    view = served(client, f"session/{observation_id}")
    assert view["km"] and view["drift"] and view["stops"] and view["series"]
    assert view["previous"] and view["gear"] and view["weather"]
    assert contract.problems(views.SessionView, view) == []
