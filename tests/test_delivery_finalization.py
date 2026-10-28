"""Delivery acknowledgement belongs to the transport result, not model output."""

import time
from datetime import time as dtime
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.messenger import DeliveredMessage, DeliveryResult
from redline.service.proactive import ProactiveJob


def _running_job(store) -> ProactiveJob:
    store.proactive.enqueue_job("job-1", "silence", "3", time.time())
    store.proactive.claim_due_job(time.time())
    return ProactiveJob(
        psid="4242", kind="silence", prompt="prends des nouvelles",
        job_id="job-1", detail="3",
    )


def test_partial_text_delivery_is_acknowledged_without_replay(make_bot, store):
    bot = make_bot(
        agent=FakeAgent(result=RunResult(full_text="Je prends de tes nouvelles.")),
        proactive_active_start=dtime(0, 0))
    bot._client.send_burst.return_value = DeliveryResult.failure(
        "second fragment refused", delivered=1)
    job = _running_job(store)

    bot._respond_proactive(job)

    assert store.proactive.jobs()[0]["status"] == "done"
    bot._repo.record_proactive_delivery.assert_not_called()
    assert not store._conversation
    bot._client.send_burst.assert_called_once()


def test_zero_delivered_fragments_release_the_job(make_bot, store):
    bot = make_bot(
        agent=FakeAgent(result=RunResult(full_text="Je prends de tes nouvelles.")),
        proactive_active_start=dtime(0, 0))
    bot._client.send_burst.return_value = DeliveryResult.failure(
        "transport unavailable", delivered=0)
    job = _running_job(store)

    bot._respond_proactive(job)

    assert store.proactive.jobs()[0]["status"] == "pending"
    bot._repo.record_proactive_delivery.assert_not_called()


def test_partial_reactive_delivery_is_acknowledged_without_rerunning_the_agent(
        make_bot, store):
    agent = FakeAgent(result=RunResult(full_text="Premier fragment.\n\nSecond fragment."))
    bot = make_bot(agent=agent)
    bot._client.send_burst.return_value = DeliveryResult.failure(
        "second fragment refused", delivered=1,
        messages=(DeliveredMessage("mid-first", "Premier fragment."),))

    processed = bot._respond("4242", "question", message_ids=("mid-in",))

    assert processed
    assert len(agent.calls) == 1


def test_session_followup_rechecks_the_delivery_window(make_bot, store):
    job = _running_job(store)
    job.kind = "session_followup"
    job.detail = '{"session_key":"2026-W36:test","predicted_trace":"aucune"}'
    bot = make_bot()
    bot._state.proactive.would_pass = lambda *args, **kwargs: False

    bot._respond_proactive(job)

    assert store.proactive.jobs()[0]["status"] == "pending"
    bot._client.send_quick_replies.assert_not_called()


def test_partial_generated_proactive_remembers_only_the_received_fragment(make_bot, store):
    bot = make_bot(
        agent=FakeAgent(result=RunResult(full_text='Première partie.\n\nTu as encore mal ?')),
        proactive_active_start=dtime(0, 0))
    bot._client.send_burst.return_value = DeliveryResult.failure(
        'second fragment refused', delivered=1,
        messages=(DeliveredMessage('mid-first', 'Première partie.'),))
    job = _running_job(store)
    job.kind = 'strava_debrief'

    bot._respond_proactive(job)

    assert store.proactive.jobs()[0]['status'] == 'done'
    assert store._conversation[-1]['text'] == 'Première partie.'
    assert bot._repo.record_proactive_delivery.call_args.args[2] == 'Première partie.'
    bot._client.send_burst.assert_called_once()


def test_partial_closed_proactive_does_not_mark_an_unreceived_question(make_bot, store):
    from redline.service.proactive_messages import ClosedProactive

    bot = make_bot(proactive_active_start=dtime(0, 0))
    bot._client.send_burst.return_value = DeliveryResult.failure(
        'second fragment refused', delivered=1,
        messages=(DeliveredMessage('mid-first', 'Pour ta sortie.'),))
    bot._mark_cells_asked = MagicMock()
    job = _running_job(store)
    bot._deliver_closed_proactive(job, ClosedProactive(
        'Pour ta sortie.\n\nQuel plaisir ?', ('2026-09-06:easy:Plaisir',),
        question='Quel plaisir ?'))

    assert store._conversation[-1]['text'] == 'Pour ta sortie.'
    bot._mark_cells_asked.assert_not_called()
    # La question n'a pas franchi Meta : elle ne devient ni suivie ni « déjà posée ».
    assert store._conversation[-1]['questions'] == ()
    assert not store.question_known('4242', 'Quel plaisir ?')
