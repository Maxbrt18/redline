"""A crashed proactive returns its durable job to the queue."""

import time
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.service.bot import CoachBot
from redline.service.proactive import ProactiveJob


def _bot(store, make_config):
    return CoachBot(client=MagicMock(), repo=MagicMock(), agent=MagicMock(provider="claude"),
                    state=store, config=make_config(), outbox=MagicMock())


def test_a_crashed_proactive_releases_its_job(store, make_config):
    bot = _bot(store, make_config)
    store.proactive.enqueue_job("weekly:2026-W29", "weekly", "", 0)
    claimed = store.proactive.claim_due_job(1)
    bot._release_job(ProactiveJob(psid="4242", kind="weekly", prompt="p",
                                  job_id=claimed["id"]))
    assert store.proactive.jobs(active_only=True)[0]["status"] == "pending"


def test_startup_recovers_a_job_left_running(store):
    store.proactive.enqueue_job("weekly:2026-W29", "weekly", "", 0)
    assert store.proactive.claim_due_job(1)["status"] == "running"

    assert store.proactive.recover_running_jobs() == 1

    recovered = store.proactive.claim_due_job(time.time())
    assert recovered["id"] == "weekly:2026-W29"
    assert recovered["attempts"] == 2


def test_an_unpersisted_test_job_is_left_alone(store, make_config):
    bot = _bot(store, make_config)
    bot._release_job(ProactiveJob(psid="4242", kind="strava_debrief", prompt="p"))
    assert store.proactive.jobs(active_only=True) == []


def test_a_stable_job_id_deduplicates_the_trigger(store):
    assert store.proactive.enqueue_job("morning:2026-07-14", "morning", "", 0)
    assert not store.proactive.enqueue_job("morning:2026-07-14", "morning", "", 0)


def test_a_queued_proactive_waits_if_the_athlete_has_started_talking(make_bot):
    agent = FakeAgent()
    bot = make_bot(agent=agent)
    bot._state.mark_inbound("4242")

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))

    assert agent.calls == []


def test_message_arriving_during_generation_keeps_the_text_and_the_session(
        make_bot, store):
    """RWF-008: the finished generation is neither thrown away nor erased from the
    conversation (the light proactive runs on a copy); it waits, prepared, until the
    athlete has finished writing."""
    agent = FakeAgent()
    bot = make_bot(agent=agent)
    original = agent.run

    def run(*args, **kwargs):
        result = original(*args, **kwargs)
        store.mark_inbound('4242')
        return result

    agent.run = run
    bot._release_job = MagicMock()
    store.set_session('4242', 'claude', 'session-before', 'digest')
    job = ProactiveJob(psid='4242', kind='strava_debrief', prompt='nouvelles', detail='42',
                       job_id='strava:42')

    bot._respond_proactive(job)

    assert len(agent.calls) == 1
    bot._client.send_burst.assert_not_called()
    bot._release_job.assert_called_once_with(job)
    assert store.session_id('4242', 'claude') == 'session-before'
    assert store.stored_outbound_delivery('proactive:strava:42')['status'] == 'prepared'
    assert not store._conversation


def test_second_activity_check_defers_before_transport(make_bot, store):
    agent = FakeAgent()
    bot = make_bot(agent=agent)
    bot._release_job = MagicMock()
    job = ProactiveJob(psid='4242', kind='strava_debrief', prompt='nouvelles', detail='42')
    checks = iter((False, True))

    def defer(*args, **kwargs):
        active = next(checks)
        if active:
            bot._release_job(job)
        return active

    bot._defer_active_proactive = MagicMock(side_effect=defer)

    bot._respond_proactive(job)

    assert bot._defer_active_proactive.call_count == 2
    bot._release_job.assert_called_once_with(job)
    bot._client.send_burst.assert_not_called()
    bot._client.send_text.assert_not_called()


def test_pending_message_still_blocks_after_the_silence_window(make_bot, store):
    bot = make_bot()
    store.mark_inbound('4242')
    bot._state.last_inbound = lambda psid: time.time() - 3600
    bot._queue.submit_inbound('4242', 'Une précision', 'incoming')
    bot._release_job = MagicMock()
    job = ProactiveJob(psid='4242', kind='strength_card', prompt='', detail='{}', priority=True)

    bot._respond_proactive(job)

    bot._release_job.assert_called_once_with(job)
    bot._client.send_text.assert_not_called()
    assert bot.has_pending_inbound()


def test_closed_proactive_rechecks_activity_when_called_directly(make_bot, store):
    from redline.service.proactive_messages import ClosedProactive

    bot = make_bot()
    bot._release_job = MagicMock()
    store.mark_inbound('4242')
    job = ProactiveJob(psid='4242', kind='silence', prompt='')
    bot._deliver_closed_proactive(job, ClosedProactive('Des nouvelles ?'))
    bot._release_job.assert_called_once_with(job)
    bot._client.send_burst.assert_not_called()


def test_card_rendered_while_a_message_arrives_is_kept_out_of_transport(make_bot, store, tmp_path):
    from unittest.mock import patch

    bot = make_bot()
    bot._strength_card_context = MagicMock(return_value=(
        'ready', '2026-09-06', {'title': 'Renfo A'}))
    bot._release_job = MagicMock()
    job = ProactiveJob(psid='4242', kind='strength_card', prompt='', priority=True)
    target = tmp_path / 'card.png'

    def render(*args, **kwargs):
        store.mark_inbound('4242')
        return target

    with patch('redline.service.bot.render_strength_artifact', side_effect=render):
        assert not bot._deliver_strength_card('4242', 'card', job=job)

    bot._release_job.assert_called_once_with(job)
    bot._outbox.archive.assert_called_once_with(target)
    bot._client.send_text.assert_not_called()
    bot._client.send_image.assert_not_called()


def test_deferred_job_can_resume_after_the_conversation_becomes_idle(make_bot, store):
    from datetime import time as dtime

    bot = make_bot(proactive_active_start=dtime(0, 0))
    store.proactive.enqueue_job('late-job', 'silence', '3', 0)
    store.proactive.claim_due_job(time.time())
    store.mark_inbound('4242')
    job = ProactiveJob(psid='4242', kind='silence', prompt='', detail='3', job_id='late-job')
    bot._respond_proactive(job)
    assert store.proactive.jobs()[0]['status'] == 'pending'

    bot._state.last_inbound = lambda psid: time.time() - 3600
    store.proactive.claim_due_job(time.time())
    bot._respond_proactive(job)
    assert store.proactive.jobs()[0]['status'] == 'done'
    bot._client.send_burst.assert_called_once()
