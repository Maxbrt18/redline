"""The weekly review goes out as soon as Sunday is settled, not at a fixed hour.

When the Sunday sessions are done early, nothing can change the review any more, so the
next week must not wait for the evening. The fixed hour remains the fallback when a
Sunday session is not settled.
"""

import datetime as dt
import json
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from redline.service.proactive import ProactiveScheduler
from redline.service.vault import VaultRepo

PARIS = ZoneInfo("Europe/Paris")
SUNDAY = dt.date(2026, 10, 18)
PSID = "4242"


def _plan(*sessions):
    return json.dumps({"sessions": [
        {"id": key, "date": day, "kind": kind, "status": "planned"}
        for key, day, kind in sessions]})


def _repo(monkeypatch, plan, links):
    store = MagicMock()
    store.week_session_links.return_value = {
        key: {"state": state} for key, state in links.items()}
    repo = VaultRepo("/tmp/vault", "origin", "main", operational_store=store)
    monkeypatch.setattr(repo, "weekly_plan_context", lambda week: plan)
    return repo


def test_a_sunday_is_settled_once_run_and_strength_are_done(monkeypatch):
    plan = _plan(("w42-sam-ef", "2026-10-18", "run"),
                 ("w42-ven-renfo", "2026-10-18", "strength"),
                 ("w42-dim-sl", "2026-10-17", "run"))
    repo = _repo(monkeypatch, plan, {"w42-sam-ef": "completed", "w42-ven-renfo": "planned"})
    assert not repo.day_settled(SUNDAY)

    repo = _repo(monkeypatch, plan, {"w42-sam-ef": "completed", "w42-ven-renfo": "missed"})
    assert repo.day_settled(SUNDAY)


def test_a_rest_sunday_is_settled_and_no_plan_never_is(monkeypatch):
    assert _repo(monkeypatch, _plan(("w42-dim-repos", "2026-10-18", "rest")), {}
                 ).day_settled(SUNDAY)
    assert not _repo(monkeypatch, "", {}).day_settled(SUNDAY)


def _weekly_queued(store, make_config, at, *, settled=True, debrief=False):
    bot = MagicMock()
    bot.day_settled.return_value = settled
    bot.wake_reminder_due.return_value = False
    bot.pending_ledger_cells.return_value = []
    if debrief:
        store.proactive.enqueue_job("strava:1", "strava_debrief", "1", at.timestamp() + 600)
    ProactiveScheduler(bot, store, make_config())._materialize_triggers(PSID, at)
    return any(job["id"] == "weekly:2026-W42"
               for job in store.proactive.jobs(active_only=True))


def test_the_weekly_is_queued_as_soon_as_sunday_is_settled(store, make_config):
    assert _weekly_queued(store, make_config, datetime(2026, 10, 18, 13, 0, tzinfo=PARIS))


def test_an_unsettled_sunday_waits_for_the_fixed_hour(store, make_config):
    assert not _weekly_queued(store, make_config, datetime(2026, 10, 18, 13, 0, tzinfo=PARIS),
                              settled=False)


def test_a_pending_debrief_goes_first(store, make_config):
    assert not _weekly_queued(store, make_config, datetime(2026, 10, 18, 13, 0, tzinfo=PARIS),
                              debrief=True)


def test_never_before_noon(store, make_config):
    assert not _weekly_queued(store, make_config, datetime(2026, 10, 18, 11, 0, tzinfo=PARIS))


def test_the_early_weekly_keeps_its_real_hour(make_bot):
    from redline.service.proactive import ProactiveJob

    bot = make_bot()
    job = ProactiveJob(psid=PSID, kind="weekly", prompt="", job_id="weekly:2026-W42")
    noon = datetime(2026, 10, 18, 12, 30, tzinfo=PARIS)
    assert bot._logical_moment(job, noon) == noon
    monday = datetime(2026, 10, 19, 7, 30, tzinfo=PARIS)
    assert bot._logical_moment(job, monday).date() == SUNDAY
