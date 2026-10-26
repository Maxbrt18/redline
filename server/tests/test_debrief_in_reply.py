"""Sam écrit pendant que le débrief de sa sortie attend : la réponse le porte.

Avant, son message repoussait le débrief de 8 min (conversation active) ; le faire
partir à côté de la réponse aurait doublé le message. Le tour réactif réclame donc le
job, lit la sortie capturée et solde le job seulement si la réponse est partie.
"""

import time
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.agent import FailureKind, RunResult
from redline.vault import PersistenceResult


def _bot(make_bot, result=None):
    client = MagicMock()
    for method in ("send_text", "send_burst", "send_image", "send_button", "action"):
        getattr(client, method).return_value = MagicMock(ok=True)
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.is_clean.return_value = True
    repo.ensure_clean.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.weekly_plan_context.return_value = ""
    repo.weekly_plan_message.return_value = "Aujourd'hui : EF, plafond 9 km."
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    repo.activity_recorded.return_value = True
    agent = FakeAgent(result=result or RunResult(full_text="Belle EF, 6:05 de moyenne."))
    return make_bot(agent=agent, client=client, repo=repo), agent, repo


def _waiting(store):
    store.proactive.record_activity("999", due=time.time() + 300)


def test_a_message_during_the_wait_carries_the_debrief(make_bot, store):
    bot, agent, repo = _bot(make_bot)
    _waiting(store)

    assert bot._respond("4242", "fini, bonnes jambes")

    prompt = agent.calls[-1]["message"]
    assert "DÉBRIEF DANS CETTE RÉPONSE" in prompt
    assert "INSTANTANÉ STRAVA CAPTURÉ" in prompt and '"id":999' in prompt
    assert prompt.index("DÉBRIEF DANS CETTE RÉPONSE") < prompt.index("fini, bonnes jambes")
    assert store.proactive.jobs(active_only=True) == []
    assert store.proactive.jobs()[0]["status"] == "done"
    repo.activity_recorded.assert_called_with("999")


def test_an_unsent_reply_gives_the_debrief_back_due_at_once(make_bot, store):
    bot, _, _ = _bot(make_bot, RunResult(error="limite", failure=FailureKind.RATE_LIMIT))
    _waiting(store)

    assert not bot._respond("4242", "fini")

    [job] = store.proactive.jobs(active_only=True)
    assert job["status"] == "pending" and job["due"] <= time.time() + 1


def test_a_failed_capture_leaves_the_reply_alone(make_bot, store):
    bot, agent, _ = _bot(make_bot)
    _waiting(store)
    store.ingest_strava_activity = MagicMock(side_effect=RuntimeError("Strava en panne"))

    assert bot._respond("4242", "fini")

    assert "DÉBRIEF" not in agent.calls[-1]["message"]
    [job] = store.proactive.jobs(active_only=True)
    assert job["status"] == "pending"


def test_a_strength_report_never_takes_the_debrief(make_bot, store):
    bot, agent, _ = _bot(make_bot)
    _waiting(store)

    bot._respond("4242", "Renfo terminé", message_ids=("strength:abc",))

    assert "DÉBRIEF" not in agent.calls[-1]["message"]
    assert store.proactive.jobs(active_only=True)[0]["status"] == "pending"


def test_a_message_with_no_debrief_waiting_changes_nothing(make_bot, store):
    bot, agent, _ = _bot(make_bot)

    assert bot._respond("4242", "salut")

    assert "DÉBRIEF" not in agent.calls[-1]["message"]
