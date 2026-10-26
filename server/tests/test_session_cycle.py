import datetime as dt

from scripts.model import session_cycle


def test_local_schedule_becomes_an_aware_instant():
    planned = session_cycle.scheduled_for("2026-08-21", "18:30")
    assert planned is not None
    assert planned.isoformat() == "2026-08-21T18:30:00+02:00"


def test_an_unfixed_slot_has_no_invented_time():
    assert session_cycle.scheduled_for(dt.date(2026, 8, 21), None) is None


def test_a_changed_plan_replans_but_a_terminal_cycle_never_resurrects():
    assert session_cycle.synchronized_state("started", "planned", changed=True) == "started"
    assert session_cycle.synchronized_state("cancelled", "planned", changed=True) == "cancelled"
    assert session_cycle.synchronized_state("planned", "done", changed=False) == "completed"
    assert session_cycle.synchronized_state("closed", "planned", changed=True) == "closed"


def test_strava_local_wall_clock_never_gains_a_fake_utc_suffix():
    parsed = session_cycle.local_wall_clock("2026-08-21T07:32:50")
    assert parsed.isoformat() == "2026-08-21T07:32:50+02:00"
