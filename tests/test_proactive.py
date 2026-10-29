"""ProactiveScheduler: durable triggers and priorities, with simulated clocks.

Fixed dates: in 2026, 13 July is a Monday -> 14 July is a Tuesday, 19 July a Sunday.
"""

import json
import time
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import redline.service.proactive as proactive_module
from redline.service.proactive import ProactiveScheduler

PSID = "4242"

TUESDAY_MORNING = datetime(2026, 7, 14, 8, 0)
TUESDAY_NOON = datetime(2026, 7, 14, 12, 0)
SUNDAY_EVENING = datetime(2026, 7, 19, 19, 30)


def scheduler(store, config):
    return ProactiveScheduler(bot=None, state=store, config=config)


def due_trigger(scheduler, now, *, allow_ordinary=True):
    """Exercise the same two phases as the runtime tick, without a test-only API."""
    scheduler._materialize_triggers(PSID, now)
    return scheduler._claim_due_trigger(PSID, now, allow_ordinary=allow_ordinary)


def suppress_silence(store):
    store.mark_inbound(PSID)  # recent contact -> no silence trigger


def test_debrief_is_top_priority_and_consumed(store, make_config):
    sched = scheduler(store, make_config())
    suppress_silence(store)
    # `due` anchors on the SIMULATED clock: the code compares against now.timestamp()
    store.proactive.record_activity("999", due=TUESDAY_NOON.timestamp() - 10)

    job = due_trigger(sched, TUESDAY_NOON)
    assert job.kind == "strava_debrief"
    assert "INSTANTANÉ STRAVA CAPTURÉ ET PERSISTÉ" in job.prompt
    assert '"id":999' in job.prompt
    assert job.detail == "999"
    assert "999" in job.prompt
    assert store.proactive.jobs(active_only=True)[0]["status"] == "running"


def test_debrief_not_due_yet(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    store.proactive.record_activity("999", due=TUESDAY_NOON.timestamp() + 600)
    assert due_trigger(sched, TUESDAY_NOON) is None
    assert store.proactive.jobs(active_only=True)[0]["status"] == "pending"


def test_a_capture_failure_never_wakes_the_coach_without_data(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    store.proactive.record_activity("999", due=TUESDAY_NOON.timestamp() - 10)
    store.ingest_strava_activity = MagicMock(
        side_effect=RuntimeError("Strava temporairement indisponible"))

    assert due_trigger(sched, TUESDAY_NOON) is None
    pending = store.proactive.jobs(active_only=True)
    assert len(pending) == 1 and pending[0]["status"] == "pending"


def test_an_activity_strava_no_longer_knows_is_abandoned_at_once(store, make_config):
    """Deleted activities were requested again 1,500 times each: a 404 does not heal
        by retrying."""
    from redline.integrations.strava import StravaNotFound

    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    store.proactive.record_activity("999", due=TUESDAY_NOON.timestamp() - 10)
    store.ingest_strava_activity = MagicMock(
        side_effect=StravaNotFound("Strava ne connaît pas /activities/999"))

    assert due_trigger(sched, TUESDAY_NOON) is None
    assert store.proactive.jobs(active_only=True) == []
    assert due_trigger(sched, TUESDAY_NOON.replace(hour=13)) is None
    assert store.ingest_strava_activity.call_count == 1


def test_weekly_on_sunday_evening_once(store, make_config):
    sched = scheduler(store, make_config())
    suppress_silence(store)
    job = due_trigger(sched, SUNDAY_EVENING)
    assert job.kind == "weekly"
    assert due_trigger(sched, SUNDAY_EVENING) is None  # marker set


def test_morning_in_the_grace_window(store, make_config):
    sched = scheduler(store, make_config())
    suppress_silence(store)
    job = due_trigger(sched, TUESDAY_MORNING)
    assert job.kind == "morning"
    assert due_trigger(sched, TUESDAY_MORNING) is None  # once a day


def test_morning_too_late_does_not_fire(store, make_config):
    sched = scheduler(store, make_config())
    suppress_silence(store)
    too_late = datetime(2026, 7, 14, 11, 0)  # 07:45 + 3 h grace exceeded
    assert due_trigger(sched, too_late) is None
    assert not any(job["kind"] == "morning" for job in store.proactive.jobs())


def test_pending_ledger_cells_accept_the_iso_date_from_the_read_model(
        store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    sched._bot = MagicMock()
    sched._bot.has_pending_inbound.return_value = False
    sched._bot.conversation_active.return_value = False
    sched._bot.pending_ledger_cells.return_value = [{
        "date": "2026-07-14",
        "type": "easy",
        "missing": ["rpe"],
    }]

    job = due_trigger(sched, TUESDAY_NOON.replace(hour=20))

    assert job.kind == "ledger_incomplete"
    assert json.loads(job.detail)["date"] == "2026-07-14"


def test_pending_wake_cells_are_not_claimed_in_the_evening(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    sched._bot = MagicMock()
    sched._bot.has_pending_inbound.return_value = False
    sched._bot.conversation_active.return_value = False
    sched._bot.pending_ledger_cells.return_value = [{
        "date": "2026-07-14", "type": "wake-trace", "missing": ["pain"],
    }]

    assert due_trigger(sched, TUESDAY_NOON.replace(hour=20)) is None
    assert not any(job["kind"] == "ledger_incomplete"
                   for job in store.proactive.jobs())


def test_objective_only_ledger_cells_never_wake_the_athlete(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    sched._bot = MagicMock()
    sched._bot.has_pending_inbound.return_value = False
    sched._bot.conversation_active.return_value = False
    sched._bot.pending_ledger_cells.return_value = [{
        "date": "2026-07-14", "type": "long", "missing": ["dminus"],
    }]

    assert due_trigger(sched, TUESDAY_NOON.replace(hour=20)) is None
    assert not any(job["kind"] == "ledger_incomplete"
                   for job in store.proactive.jobs())


def test_ledger_claim_excludes_objective_cells_from_its_question(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    sched._bot = MagicMock()
    sched._bot.has_pending_inbound.return_value = False
    sched._bot.conversation_active.return_value = False
    sched._bot.pending_ledger_cells.return_value = [{
        "date": "2026-07-14", "type": "long", "missing": ["rpe", "dminus"],
    }]

    job = due_trigger(sched, TUESDAY_NOON.replace(hour=20))

    assert json.loads(job.detail)["missing"] == ["rpe"]


def test_silence_after_n_days(store, make_config):
    config = make_config(proactive_weekly=None, proactive_morning=None,
                         proactive_silence_days=3)
    sched = scheduler(store, config)
    store.section("inbound")[PSID] = time.time() - 5 * 86400 - 60  # 5 days of silence
    job = due_trigger(sched, TUESDAY_NOON)
    assert job.kind == "silence"
    assert job.detail == "5"
    assert due_trigger(sched, TUESDAY_NOON) is None  # once a day


def test_monthly_on_first_of_month_once(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    first_august = datetime(2026, 8, 1, 19, 0)  # a Saturday, past 18:30
    job = due_trigger(sched, first_august)
    assert job.kind == "monthly"
    assert due_trigger(sched, first_august) is None  # once a month
    # the 1st of the NEXT month fires again
    first_september = datetime(2026, 9, 1, 19, 0)
    assert due_trigger(sched, first_september).kind == "monthly"


def test_monthly_never_on_another_day(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    assert due_trigger(sched, datetime(2026, 8, 3, 19, 0)) is None  # Monday 3 Aug


def test_deferred_retry_resumes_and_keeps_its_expiry(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    expire = TUESDAY_NOON.timestamp() + 3600
    store.proactive.enqueue_job("weekly:retry", "weekly", "",
                                TUESDAY_NOON.timestamp() - 5, expire_at=expire)
    job = due_trigger(sched, TUESDAY_NOON)
    assert job.kind == "weekly"
    assert job.expire_at == expire
    assert store.proactive.jobs(active_only=True)[0]["status"] == "running"


def test_expired_retry_is_dropped(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    store.proactive.enqueue_job("morning:expired", "morning", "",
                                TUESDAY_NOON.timestamp() - 100,
                                expire_at=TUESDAY_NOON.timestamp() - 50)
    assert due_trigger(sched, TUESDAY_NOON) is None
    assert store.proactive.jobs(active_only=True) == []


def test_debrief_passes_despite_the_minimum_gap(store, make_config):
    """The morning note at 07:46 was burning the 4 h gap, so the debrief of a run
        finished at 08:28 never went out (and landed stale at 11:46).
        A gap-blocked budget must still let a priority trigger through."""
    config = make_config(proactive_min_gap_hours=4)
    sched = scheduler(store, config)
    suppress_silence(store)
    store.proactive.record_activity("999", due=TUESDAY_NOON.timestamp() - 10)
    # morning note just sent: the ordinary gate is closed
    store.proactive.finish_delivery("", TUESDAY_NOON)
    assert not store.proactive.would_pass(TUESDAY_NOON, config)
    assert store.proactive.would_pass(TUESDAY_NOON, config, ignore_gap=True)

    job = due_trigger(sched, TUESDAY_NOON, allow_ordinary=False)
    assert job.kind == "strava_debrief"
    assert job.priority
    # and the send-time guardrail lets it through too
    assert store.proactive.would_pass(TUESDAY_NOON, config, ignore_gap=True)


def test_ordinary_trigger_still_held_by_the_gap(store, make_config):
    """The exemption is for the debrief only: the weekly review waits its turn."""
    sched = scheduler(store, make_config())
    suppress_silence(store)
    assert due_trigger(sched, SUNDAY_EVENING, allow_ordinary=False) is None
    job = due_trigger(sched, SUNDAY_EVENING, allow_ordinary=True)
    assert job.kind == "weekly"  # marker intact, the trigger was not lost
    assert not job.priority


def test_no_number_of_deliveries_creates_a_daily_cap(store, make_config):
    """The counter is a trace, never a send gate."""
    config = make_config(proactive_min_gap_hours=0)
    for _ in range(100):
        store.proactive.finish_delivery("", TUESDAY_NOON)

    assert store.proactive.would_pass(TUESDAY_NOON, config)


def test_tick_sends_a_heavy_job_after_the_old_window_end(
        store, make_config, monkeypatch):
    """The proactive start is a lower bound, not an end-of-day deadline."""
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return SUNDAY_EVENING

    bot = MagicMock()
    bot.has_pending_inbound.return_value = False
    bot.conversation_active.return_value = False
    store.section("inbound")[PSID] = SUNDAY_EVENING.timestamp() - 3600
    monkeypatch.setattr(proactive_module.clock, "now", FixedDatetime.now)
    monkeypatch.setattr(proactive_module.time, "time", SUNDAY_EVENING.timestamp)

    ProactiveScheduler(bot, store, make_config())._tick()

    bot.submit_proactive.assert_called_once()


def test_heavy_review_is_sent_late_inside_the_start_only_window(
        store, make_config, monkeypatch):
    """A heavy weekly review is not delayed by its cumulative timeout budget."""
    late_sunday = datetime(2026, 8, 23, 21, 29, tzinfo=ZoneInfo("Europe/Paris"))
    bot = MagicMock()
    bot.has_pending_inbound.return_value = False
    bot.conversation_active.return_value = False
    bot.pending_ledger_cells.return_value = []
    store.section("inbound")[PSID] = late_sunday.timestamp() - 3600
    monkeypatch.setattr(proactive_module.clock, "now", lambda: late_sunday)
    monkeypatch.setattr(proactive_module.time, "time", late_sunday.timestamp)

    ProactiveScheduler(bot, store, make_config())._tick()

    bot.submit_proactive.assert_called_once()


def test_tick_keeps_jobs_before_the_morning_start(store, make_config, monkeypatch):
    early_sunday = datetime(2026, 8, 23, 6, 59, tzinfo=ZoneInfo("Europe/Paris"))
    bot = MagicMock()
    bot.has_pending_inbound.return_value = False
    bot.conversation_active.return_value = False
    bot.pending_ledger_cells.return_value = []
    store.section("inbound")[PSID] = early_sunday.timestamp() - 3600
    monkeypatch.setattr(proactive_module.clock, "now", lambda: early_sunday)
    monkeypatch.setattr(proactive_module.time, "time", early_sunday.timestamp)

    config = make_config(proactive_weekly=datetime.strptime("06:00", "%H:%M").time())
    ProactiveScheduler(bot, store, config)._tick()

    bot.submit_proactive.assert_not_called()


def test_tick_persists_a_scheduled_job_during_an_active_conversation(
        store, make_config, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return SUNDAY_EVENING

    bot = MagicMock()
    bot.has_pending_inbound.return_value = True
    bot.conversation_active.return_value = True
    store.section("inbound")[PSID] = SUNDAY_EVENING.timestamp()
    monkeypatch.setattr(proactive_module.clock, "now", FixedDatetime.now)
    monkeypatch.setattr(proactive_module.time, "time", SUNDAY_EVENING.timestamp)

    ProactiveScheduler(bot, store, make_config())._tick()

    assert [(job["id"], job["status"])
            for job in store.proactive.jobs(active_only=True)] == [
        ("weekly:2026-W29", "pending")
    ]
    bot.submit_proactive.assert_not_called()


def test_a_debrief_late_inside_the_window_is_dropped(store, make_config):
    """Late for a reason of ours (service down, Claude unavailable): a 'nice session
    this morning' at 14:00 reads as a coach who was not paying attention."""
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    late = TUESDAY_NOON.timestamp() - (sched.DEBRIEF_MAX_LATE_HOURS * 3600 + 60)
    store.proactive.record_activity("999", due=late)
    assert due_trigger(sched, TUESDAY_NOON) is None
    assert store.proactive.jobs(active_only=True) == []


def test_a_late_night_session_is_not_recreated_the_next_morning(store, make_config):
    """Without an end-of-day window, an old debrief is dropped as stale."""
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    paris = ZoneInfo("Europe/Paris")
    last_night = datetime(2026, 7, 13, 22, 5, tzinfo=paris)
    store.proactive.record_activity("999", due=last_night.timestamp())

    assert due_trigger(sched, datetime(2026, 7, 14, 7, 35, tzinfo=paris)) is None
    assert store.proactive.jobs(active_only=True) == []


def test_the_deferred_debrief_happens_once(store, make_config):
    """It is emitted and consumed in the same pass: nothing can replay it the day
    after, when it would read as a coach two days behind."""
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    store.proactive.record_activity("999", due=datetime(2026, 7, 13, 22, 5).timestamp())
    due_trigger(sched, datetime(2026, 7, 14, 7, 35))
    assert due_trigger(sched, datetime(2026, 7, 14, 7, 36)) is None


def _wake_scheduler(store, make_config, due=True):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    sched._bot = MagicMock()
    sched._bot.pending_ledger_cells.return_value = []
    sched._bot.wake_reminder_due.return_value = due
    return sched


def test_a_missing_wake_trace_is_recalled_at_half_past_ten_until_two(store, make_config):
    """The logbook takes the trace until 14:00, without notification. A single
        reminder, the same morning; never in the evening."""
    sched = _wake_scheduler(store, make_config)

    sched._materialize_triggers(PSID, TUESDAY_MORNING.replace(hour=10, minute=29))
    assert not any(job["kind"] == "wake_trace" for job in store.proactive.jobs())

    sched._materialize_triggers(PSID, TUESDAY_MORNING.replace(hour=10, minute=30))
    job = next(job for job in store.proactive.jobs() if job["kind"] == "wake_trace")
    assert job["id"] == "wake-trace:2026-07-14"
    assert job["expire_at"] == datetime(
        2026, 7, 14, 14, 0, tzinfo=ZoneInfo("Europe/Paris")).timestamp()

    # One logbook read per morning, not one per minute.
    sched._materialize_triggers(PSID, TUESDAY_MORNING.replace(hour=11))
    assert sched._bot.wake_reminder_due.call_count == 1


def test_no_wake_reminder_when_the_trace_is_in_or_after_two(store, make_config):
    sched = _wake_scheduler(store, make_config, due=False)
    sched._materialize_triggers(PSID, TUESDAY_MORNING.replace(hour=10, minute=30))
    late = _wake_scheduler(store, make_config)
    late._materialize_triggers(PSID, TUESDAY_MORNING.replace(hour=14))

    late._bot.wake_reminder_due.assert_not_called()
    assert not any(job["kind"] == "wake_trace" for job in store.proactive.jobs())


# ------------------------------------------------- debrief as soon as the athlete is done

def test_an_activity_is_captured_on_arrival_without_waking_the_coach(store, make_config):
    """The activity enters the ledger right away: the logbook can ask for its
        feelings while the debrief waits for its signal or its delay."""
    sched = scheduler(store, make_config(proactive_morning=None))
    store.proactive.record_activity("999", due=time.time() + 300)
    store.ingest_strava_activity = MagicMock(return_value={"status": "matched"})

    sched._capture_arrivals()
    sched._capture_arrivals()

    store.ingest_strava_activity.assert_called_once_with(999)
    assert store.proactive.jobs(active_only=True)[0]["status"] == "pending"


def test_a_capture_failed_on_arrival_is_left_to_the_debrief(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    store.proactive.record_activity("999", due=time.time() + 300)
    store.ingest_strava_activity = MagicMock(side_effect=RuntimeError("Strava en panne"))

    sched._capture_arrivals()

    pending = store.proactive.jobs(active_only=True)
    assert len(pending) == 1 and pending[0]["status"] == "pending"


def test_the_debrief_rereads_the_title_written_after_the_capture(store, make_config):
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    store.proactive.record_activity("999", due=TUESDAY_NOON.timestamp() - 10)
    store.ingest_strava_activity = MagicMock(wraps=store.ingest_strava_activity)

    due_trigger(sched, TUESDAY_NOON)

    store.ingest_strava_activity.assert_called_once_with(999, fresh_words=True)


def test_a_failed_capture_is_retried_within_the_minute_then_every_five(store, make_config):
    """A transient failure does not cost 5 more minutes; a repeating error does not
        burn the Strava quota."""
    sched = scheduler(store, make_config(proactive_morning=None))
    suppress_silence(store)
    store.proactive.record_activity("999", due=TUESDAY_NOON.timestamp() - 10)
    store.ingest_strava_activity = MagicMock(side_effect=RuntimeError("Strava en panne"))

    due_trigger(sched, TUESDAY_NOON)
    job = store.proactive.jobs(active_only=True)[0]
    assert job["due"] - time.time() == pytest.approx(60, abs=5)

    window = ProactiveScheduler.DEBRIEF_MAX_LATE_HOURS * 3600
    store.proactive._jobs[0].update(due=TUESDAY_NOON.timestamp() - 10,
                                    expire_at=time.time() + window - 200)
    due_trigger(sched, TUESDAY_NOON)
    job = store.proactive.jobs(active_only=True)[0]
    assert job["due"] - time.time() == pytest.approx(300, abs=5)


def test_a_nudge_runs_the_loop_without_waiting_for_the_tick(store, make_config):
    sched = scheduler(store, make_config())
    ticks = []
    sched._tick = lambda: ticks.append(time.monotonic())
    sched.TICK = 3600
    sched.start()
    try:
        sched.nudge()
        deadline = time.monotonic() + 2
        while not ticks and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ticks
    finally:
        sched.stop()
    assert not sched._thread.is_alive()
