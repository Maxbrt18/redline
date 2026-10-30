"""A failed initiative is retried, but never endlessly and never by recomputing what was
already ready.

At most three claims, with a growing delay; beyond that the job is `failed` with its
cause, distinct from a successful send. Text that is already written is kept and
resent: a transport failure never costs a new model turn.
"""

import time
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.service.agent import FailureKind, RunResult
from redline.service.messenger import DeliveryResult
from redline.service.proactive import ProactiveJob
from redline.service.vault import PersistenceResult
from redline.service.worker import TurnWorker


def _claimed(store, kind, *, expire_at=0.0, key=None):
    """A job as the scheduler hands it to the bot: durable and claimed."""
    key = key or f"{kind}:test"
    store.proactive.enqueue_job(key, kind, "42", time.time() - 1, expire_at=expire_at)
    record = store.proactive.claim_due_job(time.time())
    return ProactiveJob(psid="4242", kind=kind, prompt="p", job_id=record["id"],
                        detail=record["detail"], expire_at=record["expire_at"] or None,
                        attempts=record["attempts"])


def _job(store, key):
    return next(job for job in store.proactive.jobs() if job["id"] == key)


PLAN = '{"revision": 2, "sessions": []}'


def _bot(make_bot, result, **config):
    bot = make_bot(agent=FakeAgent(result=result), **config)
    # A valid week: without it, the morning note would arm the week repair, which has
    # priority, and the test would measure another job.
    bot._repo.weekly_plan_context.return_value = PLAN
    return bot


def test_rate_limited_proactive_is_deferred_until_the_provider_returns(make_bot, store):
    retry_at = time.time() + 1800
    bot = _bot(make_bot, RunResult(
        error="limite atteinte", retry_at=retry_at, failure=FailureKind.RATE_LIMIT))

    bot._respond_proactive(_claimed(store, "weekly"))

    pending = _job(store, "weekly:test")
    assert pending["status"] == "pending"
    assert pending["due"] == retry_at
    # Expiry anchored on this first attempt, within the weekly staleness budget.
    assert 0 < pending["expire_at"] - time.time() <= TurnWorker.RETRY_STALENESS_HOURS["weekly"] * 3600


def test_expiry_stays_anchored_to_the_first_attempt(make_bot, store):
    first_expire = time.time() + 3600
    bot = _bot(make_bot, RunResult(
        error="limite", retry_at=time.time() + 60, failure=FailureKind.RATE_LIMIT))

    bot._respond_proactive(_claimed(store, "weekly", expire_at=first_expire))

    assert _job(store, "weekly:test")["expire_at"] == first_expire


def test_comeback_after_expiry_is_a_failure_with_its_cause(make_bot, store):
    bot = _bot(make_bot, RunResult(
        error="limite", retry_at=time.time() + 7200, failure=FailureKind.RATE_LIMIT))

    bot._respond_proactive(_claimed(store, "morning", expire_at=time.time() + 600))

    job = _job(store, "morning:test")
    assert job["status"] == "failed"
    assert "no longer relevant" in job["last_error"]


def test_failure_after_work_started_is_failed_not_retried(make_bot, store):
    bot = _bot(make_bot, RunResult(
        error="crash quelconque", failure=FailureKind.IN_FLIGHT, work_started=True))

    bot._respond_proactive(_claimed(store, "weekly"))

    assert _job(store, "weekly:test")["status"] == "failed"
    assert bot._client.send_text.call_count == 0
    assert bot._client.send_burst.call_count == 0


def test_retries_back_off_and_stop_at_the_third_claim(make_bot, store):
    """Retries are spaced out instead of running every 5 minutes with a model turn each time."""
    agent = FakeAgent(result=RunResult(error="modèle saturé", failure=FailureKind.PREFLIGHT))
    bot = make_bot(agent=agent)
    bot._repo.weekly_plan_context.return_value = PLAN
    delays = []
    for _ in range(3):
        job = _claimed(store, "morning")
        started = time.time()
        bot._respond_proactive(job)
        row = _job(store, "morning:test")
        if row["status"] == "pending":
            delays.append(row["due"] - started)
            store.proactive.retry_job("morning:test", time.time() - 1, row["expire_at"])

    assert [round(delay, -1) for delay in delays] == [300, 900]
    assert _job(store, "morning:test")["status"] == "failed"
    # Three claims, each with its primary profile then its fallback.
    assert len(agent.calls) == 3 * 2


def test_a_week_still_missing_after_the_sunday_review_is_retried_then_failed(make_bot, store):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True, invalid=("2026-W40",))
    repo.drain_alerts.return_value = []
    repo.head.return_value = "0123456789abcdef"
    bot = make_bot(repo=repo, agent=FakeAgent(result=RunResult(full_text="Bilan.")))

    # A real key anchors the review on its week: with «weekly:test» the expected week
    # followed the clock and the test broke on the week rollover.
    job = _claimed(store, "weekly", key="weekly:2026-W39")
    bot._respond_proactive(job)

    assert _job(store, "weekly:2026-W39")["status"] == "pending"
    assert bot._client.send_burst.call_count == 0


def test_an_undelivered_initiative_is_resent_without_a_new_model_turn(make_bot, store):
    """Expired page token, Meta unavailable: the review is not regenerated every minute
        without an expiry. The written text is kept and resent as is."""
    agent = FakeAgent(result=RunResult(full_text="Belle sortie, on en parle ?"))
    client = MagicMock()
    client.send_burst.side_effect = [DeliveryResult.failure("Send API 500"),
                                     DeliveryResult.success()]
    client.action.return_value = DeliveryResult.success()
    bot = make_bot(agent=agent, client=client, proactive_active_start=None)

    bot._respond_proactive(_claimed(store, "strava_debrief"))
    assert _job(store, "strava_debrief:test")["status"] == "pending"
    store.proactive.retry_job("strava_debrief:test", time.time() - 1, time.time() + 3600)
    bot._respond_proactive(_claimed(store, "strava_debrief"))

    assert len(agent.calls) == 1
    assert client.send_burst.call_args.args[1] == "Belle sortie, on en parle ?"
    assert _job(store, "strava_debrief:test")["status"] == "done"


def test_an_initiative_ready_while_alex_writes_is_kept_not_regenerated(make_bot, store):
    """The reactive text wins without destroying: it waits for the end of the exchange."""
    agent = FakeAgent(result=RunResult(full_text="La blessure au réveil ?"))
    bot = make_bot(agent=agent, proactive_active_start=None)
    bot._repo.weekly_plan_context.return_value = PLAN
    job = _claimed(store, "morning")
    bot._state.mark_inbound("4242")          # the athlete writes during generation

    original = bot.conversation_active
    calls = iter([False, True])
    bot.conversation_active = lambda psid: next(calls, original(psid))
    bot._respond_proactive(job)
    assert _job(store, "morning:test")["status"] == "pending"
    assert bot._client.send_burst.call_count == 0

    bot._state._inbound["4242"] = 0.0         # the conversation has gone quiet
    bot.conversation_active = original
    store.proactive.retry_job("morning:test", time.time() - 1, time.time() + 3600)
    bot._respond_proactive(_claimed(store, "morning"))

    assert len(agent.calls) == 1
    assert bot._client.send_burst.call_args.args[1].startswith("La blessure au réveil ?")
    assert _job(store, "morning:test")["status"] == "done"
