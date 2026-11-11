import pytest

from redline.integrations import strava
from redline.service import session_detail
from redline.service.activity_pipeline import ActivityPipeline


@pytest.fixture(autouse=True)
def offline_streams(monkeypatch):
    """No test reaches Strava: the streams are unavailable unless a test provides them."""
    def unavailable(activity_id):
        raise RuntimeError("streams unavailable")

    monkeypatch.setattr(session_detail, "fetch_streams", unavailable)


class Store:
    def __init__(self, *, elevation="pending", wbgt="pending", intake=None):
        self.states = {"elevation_loss_state": elevation, "wbgt_state": wbgt}
        self.intake = intake
        self.finished = []
        self.gear = []
        self.kept = None

    def ingest_strava_activity(self, activity):
        self.activity = activity
        return {"activity_id": activity.id, "status": "matched"}

    def strava_intake(self, activity_id):
        if self.intake is not None:
            return self.intake
        return self.states

    def finish_strava_enrichment(self, activity_id, kind, **values):
        self.finished.append((activity_id, kind, values))

    def strava_enrichment_state(self, activity_id):
        return {kind: {"state": "known"} for kind in ("elevation_loss", "wbgt")}

    def sync_strava_gear(self, row):
        self.gear.append(row)

    def strava_streams(self, activity_id):
        return self.kept

    def store_strava_detail(self, activity_id, *, streams=None, weather=None, actor=""):
        self.kept = streams

    def observation_feelings(self, observation_id):
        return {}


def activity():
    return {
        "id": 42, "name": "easy", "sport_type": "Run",
        "start_date_local": "2026-08-23T08:00:00Z",
        "distance": 7000, "moving_time": 2520, "total_elevation_gain": 120,
        "map": {"summary_polyline": "g|avGsvu[ICGEO"},
        "laps": [{
            "name": "Lap 1", "distance": 1000, "moving_time": 350,
            "average_heartrate": 150, "average_cadence": 85,
        }],
    }


def test_objective_enrichments_are_finished_before_the_packet_is_returned():
    store = Store()
    pipeline = ActivityPipeline(
        store,
        activity_fetch=lambda activity_id: activity(),
        stream_fetch=lambda activity_id: [250, 310, 230],
        weather_fetch=lambda weather_reading: 21.4,
    )

    result = pipeline.run(42)

    assert result["status"] == "matched"
    assert result["snapshot"]["activity"]["name"] == "easy"
    assert result["snapshot"]["laps"][0]["cadence_spm"] == 170
    assert "route_polyline" not in result["snapshot"]["activity"]
    assert (42, "elevation_loss", {"value": 140}) in store.finished
    assert (42, "wbgt", {"value": 21.4, "unavailable": False}) in store.finished


def test_a_provider_failure_is_durable_but_does_not_erase_the_activity():
    store = Store()

    def broken(_):
        raise RuntimeError("fournisseur indisponible")

    result = ActivityPipeline(
        store,
        activity_fetch=lambda activity_id: activity(),
        stream_fetch=broken,
        weather_fetch=broken,
    ).run(42)

    assert result["activity_id"] == 42
    assert all("fournisseur indisponible" in values["error"]
               for _, _, values in store.finished)


def test_a_known_enrichment_is_not_fetched_again_on_a_replay():
    store = Store(elevation="known", wbgt="known")

    def forbidden(_):
        raise AssertionError("a known enrichment is never recomputed")

    ActivityPipeline(
        store,
        activity_fetch=lambda activity_id: activity(),
        stream_fetch=forbidden,
        weather_fetch=forbidden,
    ).run(42)

    assert store.finished == []


def test_a_persisted_capture_survives_a_restart_without_refetching_strava():
    stored = strava.parse_activity(activity()).to_json()
    store = Store(elevation="known", wbgt="known", intake={
        **store_intake(stored),
        "elevation_loss_state": "known",
        "wbgt_state": "known",
    })

    def forbidden(_):
        raise AssertionError("the persisted activity must never be fetched again")

    result = ActivityPipeline(
        store, activity_fetch=forbidden, gear_fetch=forbidden).run(42)

    assert result["snapshot"]["activity"]["id"] == 42
    assert result["snapshot"]["laps"][0]["lap"] == 1
    assert store.gear == []


def store_intake(stored):
    return {
        "distilled": stored,
        "status": "matched",
        "candidate_session_keys": ["2026-W34:w34-dim-sl"],
        "observation_id": "observation-42",
    }


def test_the_used_shoe_counter_is_refreshed_with_the_activity():
    store = Store(elevation="known", wbgt="known")
    raw = {**activity(), "gear_id": "g-shoe"}
    result = ActivityPipeline(
        store, activity_fetch=lambda _: raw,
        gear_fetch=lambda gear_id: {
            "gear_id": gear_id, "name": "Trail Shoe 3", "distance_km": 123.4,
            "primary": False, "retired": False,
        }).run(42)
    assert result["gear_sync"] == "known"
    assert store.gear[0]["distance_km"] == 123.4


def test_a_gear_provider_failure_never_erases_the_activity():
    store = Store(elevation="known", wbgt="known")
    raw = {**activity(), "gear_id": "g-shoe"}

    def broken(_):
        raise RuntimeError("Strava gear indisponible")

    result = ActivityPipeline(
        store, activity_fetch=lambda _: raw, gear_fetch=broken).run(42)
    assert result["status"] == "matched"
    assert result["gear_sync"] == "failed"


def captured_store(**feelings):
    stored = strava.parse_activity({**activity(), "laps": []}).to_json()
    store = Store(elevation="known", wbgt="known", intake={
        **store_intake(stored), "elevation_loss_state": "known", "wbgt_state": "known"})
    store.observation_feelings = lambda observation_id: feelings
    return store


def test_the_debrief_rereads_the_words_written_after_the_capture():
    """Captured on arrival, the activity still had its default name; Sam renamed it."""
    store = captured_store()
    renamed = {**activity(), "name": "Footing des quais", "description": "jambes lourdes"}
    result = ActivityPipeline(store, activity_fetch=lambda _: renamed).run(42, fresh_words=True)
    assert result["snapshot"]["activity"]["name"] == "Footing des quais"
    assert result["snapshot"]["activity"]["description"] == "jambes lourdes"


def test_an_unreadable_title_keeps_the_captured_words():
    def broken(_):
        raise RuntimeError("Strava indisponible")

    result = ActivityPipeline(captured_store(), activity_fetch=broken).run(42, fresh_words=True)
    assert result["snapshot"]["activity"]["name"] == "easy"


def test_the_feelings_already_given_travel_with_the_snapshot():
    store = captured_store(rpe=3.0, pain=0.5, enjoyment=8.0)
    result = ActivityPipeline(store, activity_fetch=lambda _: activity()).run(42)
    assert result["snapshot"]["feelings"] == {"rpe": 3.0, "pain": 0.5, "enjoyment": 8.0}
    assert "feelings" not in ActivityPipeline(
        captured_store(), activity_fetch=lambda _: activity()).run(42)["snapshot"]


def test_computed_gaps_reach_the_snapshot(monkeypatch):
    import datetime as dt

    from redline.analysis import ledger
    from redline.model import weekly_plan

    class Linked(Store):
        def ingest_strava_activity(self, activity):
            return {"activity_id": activity.id, "status": "matched",
                    "observation_id": "obs"}

        def week_session_links(self, week):
            return {"w40-sam-sl": {"observation_id": "obs"}}

        def load_plan(self, week):
            return {"sessions": [{"id": "w40-sam-sl", "title": "SL", "hr_cap_bpm": 157,
                                  "distance_cap_km": 16.0}]}

    monkeypatch.setattr(ledger, "parse_ledger", lambda store: [ledger.Row(
        date=dt.date(2026, 10, 3), type="long", idempotency_key="strava:42", km=15.84,
        hours=1.72, hr_avg=164, dplus=347)])
    monkeypatch.setattr(weekly_plan, "active", lambda plan: plan)
    result = ActivityPipeline(Linked(), activity_fetch=lambda activity_id: activity(),
                              stream_fetch=lambda activity_id: [250, 310],
                              weather_fetch=lambda weather_reading: 20.0).run(42)
    assert "FC moyenne 164 pour un plafond de 157 (+7)" in result["snapshot"]["gaps"]


def test_a_gap_failure_never_blocks_the_debrief():
    class Linked(Store):
        def ingest_strava_activity(self, activity):
            return {"activity_id": activity.id, "status": "matched",
                    "observation_id": "obs"}

    result = ActivityPipeline(Linked(), activity_fetch=lambda activity_id: activity(),
                              stream_fetch=lambda activity_id: [250, 310],
                              weather_fetch=lambda weather_reading: 20.0).run(42)
    assert "gaps" not in result["snapshot"]
    assert result["snapshot"]["activity"]["name"] == "easy"


def test_a_quality_session_carries_its_reps_into_the_snapshot(monkeypatch):
    import datetime as dt

    raw = activity()
    raw["laps"] = [
        {"name": "Lap 0", "distance": 2453, "moving_time": 1062, "average_heartrate": 156},
        {"name": "Lap 1", "distance": 654, "moving_time": 184, "average_heartrate": 179},
        {"name": "Lap 2", "distance": 498, "moving_time": 206, "average_heartrate": 160},
        {"name": "Lap 3", "distance": 647, "moving_time": 182, "average_heartrate": 178},
        {"name": "Lap 4", "distance": 1621, "moving_time": 597, "average_heartrate": 154},
    ]
    threshold = {"workout": "threshold", "title": "Seuil doux en côte"}
    monkeypatch.setattr(ActivityPipeline, "_gaps",
                        lambda self, a, o, s: ([], threshold, dt.date(2026, 10, 15)))
    snapshot = ActivityPipeline(Store(), activity_fetch=lambda _: raw,
                                stream_fetch=lambda _: [1, 2],
                                weather_fetch=lambda _: None).run(42)["snapshot"]

    assert snapshot["quality"] is True
    card = snapshot["quality_card"]
    assert card["date"] == "15/10" and len(card["reps"]) == 2
    assert card["reps"][0]["recovery"] == {"time_s": 206.0, "dist_m": 498.0}


def test_an_easy_run_has_no_quality_flag(monkeypatch):
    monkeypatch.setattr(ActivityPipeline, "_gaps",
                        lambda self, a, o, s: ([], {"workout": "easy"}, None))
    snapshot = ActivityPipeline(Store(), activity_fetch=lambda _: activity(),
                                stream_fetch=lambda _: [1, 2],
                                weather_fetch=lambda _: None).run(42)["snapshot"]
    assert "quality" not in snapshot and "quality_card" not in snapshot


def run_stream(segments):
    """Compact 1 Hz streams from (seconds, speed m/s, HR) segments, with a gentle climb."""
    time, distance, altitude, speed, hr, metres = [], [], [], [], [], 0.0
    for seconds, velocity, beat in segments:
        for _ in range(seconds):
            if time:
                metres += velocity
            time.append(len(time))
            distance.append(metres)
            altitude.append(200 + len(time) * 0.01)
            speed.append(velocity)
            hr.append(beat)
    raw = {"time": {"data": time}, "distance": {"data": distance},
           "altitude": {"data": altitude}, "heartrate": {"data": hr},
           "velocity_smooth": {"data": speed}, "cadence": {"data": [84] * len(time)}}
    return session_detail.compact(raw, [{"start_date": "2026-10-26T09:19:53Z",
                                         "elapsed_time": time[-1]}])


EVEN = [(2000, 3.0, 145)]


def test_the_streams_fetched_at_capture_are_kept_and_read_km_by_km():
    store = Store(wbgt="known")
    streams = run_stream(EVEN)
    snapshot = ActivityPipeline(
        store, activity_fetch=lambda _: activity(), detail_fetch=lambda _: streams,
        stream_fetch=lambda _: pytest.fail("the kept altitude serves the D-"),
    ).run(42)["snapshot"]
    assert store.kept is streams
    table = snapshot["km_splits"]
    assert table["columns"] == ["km", "pace", "hr", "dplus", "cadence"]
    assert table["rows"][0] == [1, "5:33", 145, 3, 168]
    assert len(table["rows"]) == 6
    assert snapshot["drift"] == {"first": 145, "second": 145}
    assert "stops" not in snapshot
    assert store.finished[0][1] == "elevation_loss"


def test_kept_streams_are_read_again_without_a_second_provider_call():
    store = Store(elevation="known", wbgt="known")
    store.kept = run_stream(EVEN)
    snapshot = ActivityPipeline(
        store, activity_fetch=lambda _: activity(),
        detail_fetch=lambda _: pytest.fail("the streams are fetched once")).run(42)["snapshot"]
    assert len(snapshot["km_splits"]["rows"]) == 6


def test_unavailable_streams_leave_the_debrief_without_its_kilometres():
    store = Store(elevation="known", wbgt="known")
    result = ActivityPipeline(store, activity_fetch=lambda _: activity()).run(42)
    assert result["status"] == "matched" and store.kept is None
    assert "km_splits" not in result["snapshot"] and "drift" not in result["snapshot"]


def test_a_long_run_table_stays_bounded():
    table = session_detail.km_table([
        {"n": n, "dist_m": 1000, "moving_s": 360, "pace_s": 360, "hr": 140, "cadence": 168,
         "dplus": 5} for n in range(1, 121)])
    assert len(table["rows"]) == 40
    assert table["rows"][0] == ["1-3", "6:00", 140, 15, 168]


def test_an_easy_run_too_fast_shows_in_the_gaps(monkeypatch):
    import datetime as dt

    from redline.analysis import ledger
    from redline.model import weekly_plan

    class Linked(Store):
        def ingest_strava_activity(self, activity):
            return {"activity_id": activity.id, "status": "matched", "observation_id": "obs"}

        def week_session_links(self, week):
            return {"w40-mer-ef": {"observation_id": "obs"}}

        def load_plan(self, week):
            return {"sessions": [{"id": "w40-mer-ef", "title": "EF", "workout": "easy",
                                  "hr_cap_bpm": 150}]}

    monkeypatch.setattr(ledger, "parse_ledger", lambda store: [ledger.Row(
        date=dt.date(2026, 10, 7), type="easy", idempotency_key="strava:42", km=6.0,
        hours=0.55, hr_avg=148, dplus=20)])
    monkeypatch.setattr(weekly_plan, "active", lambda plan: plan)
    # First kilometre at 3.6 m/s (4:37) and 160 bpm, then 3.0 m/s (5:33) at 145.
    streams = run_stream([(278, 3.6, 160), (1700, 3.0, 145)])
    gaps = ActivityPipeline(Linked(elevation="known", wbgt="known"),
                            activity_fetch=lambda _: activity(),
                            detail_fetch=lambda _: streams).run(42)["snapshot"]["gaps"]
    assert "FC au-dessus du plafond de 150 sur 1 km (km 1), jusqu'à 160" in gaps
    assert any(gap.startswith("départ rapide : 1er km en 4:37") for gap in gaps)
