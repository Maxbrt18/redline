from redline.integrations import strava
from redline.service.activity_pipeline import ActivityPipeline


class Store:
    def __init__(self, *, elevation="pending", wbgt="pending", intake=None):
        self.states = {"elevation_loss_state": elevation, "wbgt_state": wbgt}
        self.intake = intake
        self.finished = []
        self.gear = []

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
        weather_fetch=lambda meteo: 21.4,
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
        raise AssertionError("un enrichissement connu ne se recalcule pas")

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
        raise AssertionError("l'activité persistée ne doit jamais être rappelée")

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


def test_the_pipeline_uses_the_same_wall_clock_contract_as_strava():
    query = strava.parse_activity(activity()).weather_query
    assert query["time"] == "2026-08-23T08:00:00"


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
                              weather_fetch=lambda meteo: 20.0).run(42)
    assert "FC moyenne 164 pour un plafond de 157 (+7)" in result["snapshot"]["gaps"]


def test_a_gap_failure_never_blocks_the_debrief():
    class Linked(Store):
        def ingest_strava_activity(self, activity):
            return {"activity_id": activity.id, "status": "matched",
                    "observation_id": "obs"}

    result = ActivityPipeline(Linked(), activity_fetch=lambda activity_id: activity(),
                              stream_fetch=lambda activity_id: [250, 310],
                              weather_fetch=lambda meteo: 20.0).run(42)
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
    seuil = {"workout": "threshold", "title": "Seuil doux en côte"}
    monkeypatch.setattr(ActivityPipeline, "_gaps",
                        lambda self, a, o: ([], seuil, dt.date(2026, 10, 15)))
    snapshot = ActivityPipeline(Store(), activity_fetch=lambda _: raw,
                                stream_fetch=lambda _: [1, 2],
                                weather_fetch=lambda _: None).run(42)["snapshot"]

    assert snapshot["quality"] is True
    card = snapshot["quality_card"]
    assert card["date"] == "15/10" and len(card["reps"]) == 2
    assert card["reps"][0]["recovery"] == {"time_s": 206.0, "dist_m": 498.0}


def test_an_easy_run_has_no_quality_flag(monkeypatch):
    monkeypatch.setattr(ActivityPipeline, "_gaps",
                        lambda self, a, o: ([], {"workout": "easy"}, None))
    snapshot = ActivityPipeline(Store(), activity_fetch=lambda _: activity(),
                                stream_fetch=lambda _: [1, 2],
                                weather_fetch=lambda _: None).run(42)["snapshot"]
    assert "quality" not in snapshot and "quality_card" not in snapshot
