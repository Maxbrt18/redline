"""The heartbeat is the worker's pulse: it stops when nothing answers the athlete."""

import time
from unittest.mock import MagicMock

from redline.service import proactive
from redline.service.proactive import ProactiveJob, ProactiveScheduler
from redline.service.routing import RoutingConfig

PING_URL = "https://hc.example/ping/abc"


def _running(bot):
    bot._thread = MagicMock()
    bot._thread.is_alive.return_value = True
    return bot


def test_the_ceiling_counts_every_attempt_the_follow_up_and_the_triage():
    routing = RoutingConfig.default("claude", agent_timeout=600, agent_timeout_deep=1800,
                                    agent_timeout_heavy=5400, triage_timeout=10)
    assert routing.job_ceiling_seconds() == 10 + 3 * 1800
    assert routing.job_ceiling_seconds(heavy=True) == 10 + 3 * 5400


def test_a_stopped_worker_is_a_stall(make_bot):
    assert make_bot().stall() == "worker thread stopped"


def test_an_idle_or_busy_worker_within_its_ceiling_answers(make_bot):
    bot = _running(make_bot())
    assert bot.stall() == ""
    bot._busy_since = time.time() - 600
    assert bot.stall() == ""


def test_a_reply_past_every_timeout_is_a_stall_but_an_initiative_has_longer(make_bot):
    bot = _running(make_bot())
    beyond = (bot._config.routing.job_ceiling_seconds()
              + bot.STALL_MARGIN_SECONDS + 1)
    bot._busy_since = time.time() - beyond
    assert bot.stall().startswith("one job running for")
    bot._busy_heavy = True
    assert bot.stall() == ""


def test_a_failure_outside_the_handlers_costs_one_job_not_the_worker(make_bot):
    bot = make_bot()
    jobs = iter([ProactiveJob("4242", "morning", "x"), None])
    bot._queue = MagicMock()
    bot._queue.next.side_effect = lambda: next(jobs)
    bot._handle = MagicMock(side_effect=RuntimeError("boom"))
    bot._worker()
    assert bot._handle.call_count == 1
    assert bot._busy_since is None


def _scheduler(make_config, store, bot, monkeypatch):
    pings = []
    monkeypatch.setattr(proactive.requests, "get",
                        lambda url, timeout: pings.append(url))
    scheduler = ProactiveScheduler(bot, store, make_config(healthcheck_ping_url=PING_URL))
    return scheduler, pings


def test_the_heartbeat_goes_out_while_the_worker_answers(make_config, make_bot, store,
                                                          monkeypatch):
    scheduler, pings = _scheduler(make_config, store, _running(make_bot()), monkeypatch)
    scheduler._ping_healthcheck()
    assert pings == [PING_URL]


def test_the_heartbeat_is_withheld_while_the_worker_is_stalled(make_config, make_bot, store,
                                                                monkeypatch):
    scheduler, pings = _scheduler(make_config, store, make_bot(), monkeypatch)
    scheduler._ping_healthcheck()
    assert pings == []


def test_the_first_heartbeat_goes_out_right_after_boot(make_config, make_bot, store,
                                                        monkeypatch):
    """The monotonic clock starts near zero when the host boots."""
    scheduler, pings = _scheduler(make_config, store, _running(make_bot()), monkeypatch)
    monkeypatch.setattr(proactive.time, "monotonic", lambda: 12.0)
    scheduler._ping_healthcheck()
    assert pings == [PING_URL]
    scheduler._ping_healthcheck()
    assert pings == [PING_URL]
