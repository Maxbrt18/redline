"""Session detail for a completed run: stream computations, then the notebook view.

The laps are those of a hill threshold session (4 x 3 min): the reading must find four
repetitions of 28 to 30 m of climb, the last one at 4:54 within the spread of the previous
ones, and a cool-down that includes the last recovery.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os

import pytest
import sqlalchemy as sa
from conftest import ENGINE_ROOT, TEST_VAULT
from test_notebook_board import Repo
from test_weekly_plan import revision, session

from redline.model import hr_zones, instance
from redline.model.activity import Activity, Lap
from redline.service import session_detail
from redline.service.notebook_board import AnswerError, Board

URL = os.environ.get("TEST_DATABASE_URL", "")
SAM = instance.load(TEST_VAULT)          # resting HR 55, max HR 190
PARIS = dt.timezone(dt.timedelta(hours=1))


def lap(moving, km, hr, dplus, cadence):
    minutes, seconds = moving.split(":")
    return Lap(moving_s=int(minutes) * 60 + int(seconds), distance_m=round(km * 1000, 1),
               hr_avg_bpm=hr, elevation_gain_m=dplus, cadence_spm=cadence)


SEUIL_LAPS = [
    lap("15:36", 2.284, 150.4, 17.1, 164.2), lap("3:01", 0.62, 179.5, 28.7, 166.6),
    lap("3:41", 0.585, 162.5, 0.1, 164.2), lap("3:01", 0.597, 184.0, 28.3, 171.6),
    lap("3:46", 0.635, 165.4, 0.1, 162.4), lap("3:02", 0.612, 183.0, 28.8, 165.6),
    lap("3:37", 0.63, 165.8, 0.1, 162.4), lap("3:07", 0.635, 183.7, 30.3, 163.4),
    lap("3:52", 0.577, 166.6, 14.0, 156.4), lap("9:03", 1.485, 155.3, 3.9, 163.0),
]


def stream(seconds=760, stop_at=None, stop_s=30, speed=3.0, hr=150, climb=0.0):
    """A 1 Hz stream: constant speed and HR, an optional stop, a gentle slope."""
    time, distance, altitude, clock, metres = [], [], [], 0, 0.0
    for k in range(seconds):
        if k and k == stop_at:
            clock += stop_s
        elif k:
            clock += 1
            metres += speed
        time.append(clock)
        distance.append(metres)
        altitude.append(200 + climb * k)
    raw = {"time": {"data": time}, "distance": {"data": distance},
           "altitude": {"data": altitude}, "heartrate": {"data": [hr] * seconds},
           "velocity_smooth": {"data": [speed] * seconds}, "cadence": {"data": [82] * seconds}}
    return session_detail.compact(raw, [{"start_date": "2026-10-26T09:19:53Z",
                                         "elapsed_time": time[-1]}])


def test_the_zones_are_karvonen_on_the_instance_heart_rates():
    assert SAM.hr_bounds == hr_zones.bounds(55, 190) == (136, 149, 163, 176)
    assert [hr_zones.zone(hr, SAM.hr_bounds)
            for hr in (130, 136, 148, 149, 175, 185)] == [1, 2, 2, 3, 4, 5]


def test_the_threshold_reads_four_reps_and_no_final_acceleration():
    read = session_detail.quality(SEUIL_LAPS, None)
    assert [phase["kind"] for phase in read["phases"]] == [
        "warmup", "rep", "recovery", "rep", "recovery", "rep", "recovery", "rep", "cooldown"]
    reps = read["reps"]
    assert [rep["dplus"] for rep in reps] == [29, 28, 29, 30]
    assert [rep["pace_s"] for rep in reps] == [292, 303, 297, 294]
    assert [rep["hr"] for rep in reps] == [180, 184, 183, 184]
    assert read["phases"][0]["time_s"] == 936 and read["phases"][-1]["time_s"] == 232 + 543
    assert read["reading"] == {"last": 294, "fastest_before": 292, "slowest_before": 303,
                               "final_acceleration": False}


def test_a_last_rep_faster_than_all_the_others_is_a_final_acceleration():
    laps = list(SEUIL_LAPS)
    laps[7] = dataclasses.replace(laps[7], moving_s=170)
    assert session_detail.quality(laps, None)["reading"]["final_acceleration"] is True


def test_the_stream_gives_kilometres_moving_time_zones_and_stops():
    flow = stream(seconds=760, stop_at=300, stop_s=45)
    splits = session_detail.km_splits(flow)
    # 2,274 m: the last started kilometre (274 m) is merged into the previous one.
    assert [split["dist_m"] for split in splits] == [1000, 1274]
    assert splits[0]["pace_s"] == 333 and splits[0]["hr"] == 150 and splits[0]["cadence"] == 164
    assert session_detail.zone_seconds(flow, SAM.hr_bounds) == [0, 0, 759 - 1, 0, 0]
    assert session_detail.stops(flow) == [[299, 344]]
    summary = session_detail.stop_summary(flow)
    assert summary["count"] == 1 and summary["total_s"] == 45 and summary["longest_km"] == 1
    assert session_detail.drift(flow) is None  # too short to read a drift


def test_the_chart_series_breaks_its_line_during_a_stop():
    flow = stream(seconds=760, stop_at=300, stop_s=200)
    series = session_detail.series(flow, points=50)
    assert series["duration"] == flow["t"][-1]
    assert None in series["hr"] and series["hr"][0] == 150


def test_reps_sit_on_the_lap_timestamps_not_on_strava_s_stream_indices():
    """Strava `start_index` values (0-765, 766-946...) placed the first repetition 168 s too
    early; the start time of each lap places it on its own climb, from 935 to 1116 s."""
    starts = [0, 935, 1116, 1337, 1518, 1744, 1925, 2142, 2329, 2561]
    elapsed = [936, 181, 221, 181, 226, 181, 217, 187, 232, 543]
    laps = [{"start_date": (dt.datetime(2026, 10, 26, 9, 19, 53, tzinfo=dt.UTC)
                            + dt.timedelta(seconds=start)).isoformat().replace("+00:00", "Z"),
             "elapsed_time": seconds, "start_index": start - 170 if start else 0}
            for start, seconds in zip(starts, elapsed, strict=True)]
    flow = {"t": list(range(3105)), "lap_times": session_detail.lap_times(laps)}
    reps = session_detail.quality(SEUIL_LAPS, flow)["reps"]
    assert [(rep["start"], rep["end"]) for rep in reps] == [
        (935, 1116), (1337, 1518), (1744, 1925), (2142, 2329)]
    assert session_detail.lap_times([{"elapsed_time": 10}]) == []


def test_streams_kept_before_lap_timestamps_are_fetched_again(monkeypatch):
    kept = {}

    class Store:
        def store_strava_detail(self, activity_id, **values):
            kept.update(values)

    monkeypatch.setattr(session_detail, "fetch_streams", lambda _: stream(seconds=100))
    old = {"t": [0, 1], "laps": [[0, 1]]}
    intake = session_detail.ensure(Store(), {"activity_id": 1, "distilled": {}, "streams": old,
                                             "weather": {"temp": 6}})
    assert "lap_times" in intake["streams"] and set(kept) == {"streams"}


def test_a_provider_failure_leaves_the_detail_without_its_chart(monkeypatch):
    def broken(_):
        raise RuntimeError("Strava injoignable")

    kept = {}

    class Store:
        def store_strava_detail(self, activity_id, **values):
            kept.update(values)

    monkeypatch.setattr(session_detail, "fetch_streams", broken)
    intake = session_detail.ensure(Store(), {"activity_id": 1, "distilled": {}})
    assert intake["streams"] is None and kept == {}


# ------------------------------------------------------------ the notebook view
pytestmark_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")


@pytest.fixture
def store():
    from redline.storage import Database, OperationalStore
    from redline.storage.schema import metadata

    database = Database(URL, application_name="pytest")
    with database.engine.begin() as connection:
        names = ", ".join(f"redline.{table.name}" for table in metadata.sorted_tables)
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('sam')"))
        # The watched zone of the fictitious instance, as the service synchronises it.
        connection.execute(sa.text(
            "INSERT INTO redline.pain_zones(athlete_id, slug, label) VALUES ('sam', 'injury', 'Injury')"))
    yield OperationalStore(database)
    database.dispose()


def seed_threshold(store):
    store.append_plan_revision("2026-W44", revision(sessions=[
        session("w44-lun-seuil", date="2026-10-26", workout="threshold", title="Seuil en côte",
                distance_target_km=None, distance_cap_km=9.5, duration_cap_min=60,
                rpe_min=6, rpe_max=7),
    ], computed_at="2026-10-25T20:00:00+01:00", volume_target_km=48.0))
    store.ingest_strava_activity(Activity(
        id=90000000006, sport="Run", name="Fractionné seuil en côte",
        start_local="2026-10-26T10:19:53", distance_m=8655.0, moving_s=3073,
        elevation_gain_m=151.7, hr_avg_bpm=163.2, hr_max_bpm=195.0, cadence_spm=163.8,
        laps=tuple(SEUIL_LAPS)))
    if observation_of(store, 90000000006) is None:
        store.match_session("2026-W44:w44-lun-seuil", 90000000006)
    return observation_of(store, 90000000006)


def observation_of(store, activity_id):
    return next((row["id"] for row in store.observation_rows()
                 if row["strava_activity_id"] == activity_id), None)


@pytestmark_db
def test_a_threshold_detail_reads_its_reps_and_asks_for_the_reserve_once(store):
    observation_id = seed_threshold(store)
    when = dt.datetime(2026, 10, 26, 20, 0, tzinfo=PARIS)
    board = Board(store, Repo(store), ENGINE_ROOT, when.date(), when, SAM)
    intake = store.strava_detail(observation_id)
    store.store_strava_detail(intake["activity_id"], streams=stream(seconds=900, hr=170))
    view = board.session_view(str(observation_id), store.strava_detail(observation_id))
    assert view["quality"] and view["title"] == "Seuil en côte"
    assert [rep["n"] for rep in view["reps"]] == [1, 2, 3, 4]
    assert view["km"] is None and view["zones"]["seconds"][3] > 0
    assert view["week"]["km_target"] == 48.0 and view["week"]["current"]
    ask = view["reserve_ask"]
    assert ask == f"reserve:{observation_id}"
    with pytest.raises(AnswerError):
        board.answer(ask, 2)
    assert board.answer(ask, 1) == {"alert": None}
    again = Board(store, Repo(store), ENGINE_ROOT, when.date(), when)
    view = again.session_view(str(observation_id), store.strava_detail(observation_id))
    assert view["fact"]["reserve"] == 1 and view["reserve_ask"] is None
    with pytest.raises(AnswerError):
        again.answer(ask, 0)


@pytestmark_db
def test_the_reserve_is_not_asked_once_the_session_is_old(store):
    observation_id = seed_threshold(store)
    later = dt.datetime(2026, 10, 29, 9, 0, tzinfo=PARIS)
    board = Board(store, Repo(store), ENGINE_ROOT, later.date(), later)
    view = board.session_view(str(observation_id), store.strava_detail(observation_id))
    assert view["reserve_ask"] is None and view["series"] is None


@pytestmark_db
def test_the_route_serves_the_detail_and_refuses_what_is_not_an_identifier(
        store, make_config, monkeypatch):
    from unittest.mock import MagicMock

    from redline.service import clock
    from redline.service.notebook import NotebookService
    from redline.service.web import create_app
    from redline.storage.notebook import NotebookStore

    observation_id = seed_threshold(store)
    evening = dt.datetime(2026, 10, 26, 20, 0, tzinfo=PARIS)
    monkeypatch.setattr(clock, "now", lambda: evening)
    monkeypatch.setattr(clock, "today", lambda: evening.date())
    monkeypatch.setattr(session_detail, "fetch_streams", lambda _: stream(seconds=600))
    config = make_config()
    archive = NotebookStore(store.database)
    service = NotebookService(archive, Repo(store), MagicMock(), ENGINE_ROOT,
                              "https://coach.test", operational=store)
    client = create_app(config, MagicMock(), MagicMock(), notebook=service).test_client()
    assert client.get(f"/carnet-api/session/{observation_id}").status_code == 401
    client.post("/carnet-api/connect", json={"token": archive.issue(config.allowed_psid)})
    assert client.get("/carnet-api/session/drop-table").status_code == 400
    assert client.get("/carnet-api/session/00000000-0000-0000-0000-000000000000").status_code == 404
    view = client.get(f"/carnet-api/session/{observation_id}").json
    assert view["series"]["hr"] and view["reps"]
    # The streams fetched on first display are kept: the second one does not ask for them again.
    monkeypatch.setattr(session_detail, "fetch_streams", lambda _: pytest.fail("relevé deux fois"))
    assert client.get(f"/carnet-api/session/{observation_id}").status_code == 200
    answer = client.post("/carnet-api/answer", json={"ask": view["reserve_ask"], "value": 0})
    assert answer.json == {"status": "ok", "alert": False}
