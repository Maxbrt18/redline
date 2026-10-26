"""User-facing control plane: /deep, /status and /clear.

These are transport guarantees, not coaching semantics. In particular /deep is
explicit, one-shot, serialized with every other job, and hands its exchange back
to the ordinary conversation exactly once.
"""

import json
import time
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.agent import AgentActivity, RunResult, SessionMode
from redline.plans import PlanStatus
from redline.prompts import DEPTH_NOTES
from redline.vault import PersistenceResult
from redline.work_queue import DeepRequest, InboundRequest, WorkQueue


def test_deep_is_immediate_and_absorbs_the_unripe_burst(make_bot):
    bot = make_bot(agent=FakeAgent())
    bot.submit_inbound("4242", "Le contexte juste avant")
    bot.submit_inbound("4242", "/deep Est-ce que le raisonnement tient ?")

    job = bot._queue.next()
    assert isinstance(job, DeepRequest)
    assert job.text == "Le contexte juste avant\nEst-ce que le raisonnement tient ?"
    assert not bot.has_pending_inbound()


def test_deep_routes_only_that_turn_to_the_heavy_fork(make_bot, store):
    store.set_session("4242", "codex", "ordinary-thread")
    agent = FakeAgent(result=RunResult(full_text="Analyse faite."))
    bot = make_bot(agent=agent)

    bot._respond("4242", "Reprends le raisonnement", deep_request=True)

    call = agent.calls[0]
    assert call["model"] == "gpt-6-sol"
    assert call["session_mode"] is SessionMode.FORK
    assert call["effort"] == "high"
    assert call["timeout"] == 1800
    assert DEPTH_NOTES["N3"] in call["message"]
    assert store.session_id("4242", "codex") == "ordinary-thread"
    assert "Analyse faite" in store.handoff("4242")


def test_automatic_n3_stays_on_the_ordinary_model(make_bot):
    agent = FakeAgent(depth="N3")
    bot = make_bot(agent=agent)

    bot._respond("4242", "Tu es sûr ?")

    call = agent.calls[0]
    assert call["model"] == "gpt-6-sol"
    assert call["session_mode"] is SessionMode.CONTINUE
    assert call["effort"] == "high"


def test_deep_handoff_is_injected_once_into_the_ordinary_thread(make_bot, store):
    store.set_handoff("4242", "QUESTION /deep : pourquoi ?\nRÉPONSE : parce que.")
    agent = FakeAgent(result=RunResult(full_text="La suite."))
    bot = make_bot(agent=agent)

    bot._respond("4242", "Et donc demain ?")

    assert "PASSERELLE D'UNE ANALYSE /deep" in agent.calls[0]["message"]
    assert "RÉPONSE : parce que" in agent.calls[0]["message"]
    assert store.handoff("4242") == ""


def test_a_deep_write_expires_the_stale_ordinary_session(make_bot, store):
    store.set_session("4242", "codex", "ordinary-thread")
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.head.side_effect = ["before", "after"]
    repo.weekly_plan_context.return_value = ""
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.pending_ledger_cells.return_value = []
    agent = FakeAgent(result=RunResult(full_text="Décision enregistrée."))
    bot = make_bot(agent=agent, repo=repo)

    bot._respond("4242", "Tranche et consigne", deep_request=True)

    assert store.session_id("4242", "codex") is None
    assert "Décision enregistrée" in store.handoff("4242")


def test_status_is_actionable_and_never_exposes_the_session_id(make_bot, store):
    store.set_session("4242", "codex", "secret-session-uuid")
    bot = make_bot(agent=FakeAgent())

    text = bot._status_text("4242")

    assert "N1 : gpt-6-luna/high" in text
    assert "Session active" in text and "contexte frais dans" in text
    assert "/deep : gpt-6-sol/high" in text
    assert "secret-session-uuid" not in text


def test_status_treats_an_expired_session_as_inactive(make_bot, store):
    store.set_session("4242", "codex", "old")
    store.section("sessions")["4242"]["last"] = time.time() - 7 * 3600
    bot = make_bot(agent=FakeAgent(), session_ttl_hours=6)
    assert "Aucune session active" in bot._status_text("4242")


def test_status_describes_the_live_turn_and_cancel_stops_that_exact_turn(make_bot):
    class BusyAgent(FakeAgent):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        def activity(self, psid):
            return AgentActivity("gpt-6-sol", "medium", time.monotonic() - 125, 1800)

        def cancel(self, psid):
            self.cancelled = True
            return True

    agent = BusyAgent()
    client = MagicMock()
    from redline.messenger import DeliveryResult
    client.send_text.return_value = DeliveryResult.success()
    bot = make_bot(agent=agent, client=client)

    status = bot._status_text("4242")
    assert "gpt-6-sol/medium" in status
    assert "depuis 2 min" in status
    assert "watchdog dans 27 min" in status

    assert bot._run_command("4242", "/cancel", message_ids=("mid-cancel",))
    assert agent.cancelled is True
    assert "sans être relancé" in client.send_text.call_args.args[1]


def test_bare_deep_explains_the_non_stateful_syntax(make_bot):
    client = MagicMock()
    bot = make_bot(agent=FakeAgent(), client=client)
    bot._run_command("4242", "/deep")
    assert "/deep <ta question>" in client.send_text.call_args.args[1]


def test_a_command_retry_keeps_one_outbound_identity(make_bot, store):
    from redline.messenger import DeliveryResult

    client = MagicMock()
    client.send_text.side_effect = [
        DeliveryResult.failure("réseau", delivered=0), DeliveryResult.success()]
    bot = make_bot(client=client)

    assert not bot._run_command("4242", "/help", message_ids=("mid-command",))
    assert bot._run_command("4242", "/help", message_ids=("mid-command",))
    assert client.send_text.call_count == 2
    assert len(store._deliveries) == 1


def test_clear_drops_the_session_handoff_and_queued_deep(make_bot, store):
    store.set_session("4242", "codex", "thread")
    store.set_handoff("4242", "bridge")
    bot = make_bot(agent=FakeAgent())
    bot.submit_inbound("4242", "/deep encore")

    bot.submit_inbound("4242", "/clear")
    command = bot._queue.next()
    bot._run_command(command.psid, command.command)

    assert store.session_id("4242", "codex") is None
    assert store.handoff("4242") == ""
    assert not bot.has_pending_inbound()


def test_text_that_merely_mentions_renfo_is_not_a_command():
    queue = WorkQueue(0, 0)
    queue.submit_inbound("4242", "Tu peux m'expliquer la renfo de demain ?", "mid")

    job = queue.next()
    assert isinstance(job, InboundRequest)
    assert job.text == "Tu peux m'expliquer la renfo de demain ?"


def test_exact_renfo_command_is_deterministic_and_idempotent(
        make_bot, monkeypatch, tmp_path):
    from redline.outbox import Outbox

    today = __import__("datetime").date.today().isoformat()
    context = json.dumps({
        "revision": 4,
        "sessions": [{
            "id": "w36-renfo-a", "title": "Renfo A", "date": today,
            "kind": "strength", "workout": "renfo-a", "status": "planned",
            "demanding": False, "strength": {}, "sources": ["[[strength]]"],
        }],
        "gates": [],
    })
    outbox = Outbox(tmp_path / "outbox")

    def render(session, selected_outbox, day):
        target = selected_outbox.target(f"strength-{day}.png")
        target.write_bytes(b"png")
        return target

    monkeypatch.setattr("redline.bot.render_strength_artifact", render)
    agent = FakeAgent()
    client = MagicMock()
    from redline.messenger import DeliveryResult
    client.send_text.return_value = DeliveryResult.success()
    client.send_image.return_value = DeliveryResult.success()
    bot = make_bot(agent=agent, client=client, outbox=outbox)
    bot._repo.weekly_plan_context.return_value = context

    assert bot._run_command("4242", "/renfo", message_ids=("mid-renfo",))
    assert bot._run_command("4242", "/renfo", message_ids=("mid-renfo",))

    assert agent.calls == []
    assert client.send_image.call_count == 1


def test_renfo_command_never_translates_an_invalid_plan_to_no_session(make_bot):
    client = MagicMock()
    from redline.messenger import DeliveryResult
    client.send_text.return_value = DeliveryResult.success()
    bot = make_bot(client=client)
    bot._repo.validate_weekly_plan.return_value = PlanStatus.INVALID

    assert bot._run_command("4242", "/renfo", message_ids=("mid-invalid",))

    message = client.send_text.call_args.args[1]
    assert "pas de programme fiable pour aujourd'hui" in message
    assert "absent ou invalide" not in message
    assert "Aucune séance" not in message


def test_renfo_command_names_a_held_session_instead_of_denying_it(make_bot):
    today = __import__("datetime").date.today().isoformat()
    context = json.dumps({
        "revision": 4,
        "sessions": [{
            "id": "w36-renfo-a", "title": "Renfo A", "date": today,
            "kind": "strength", "workout": "renfo-a", "status": "planned",
            "demanding": False, "strength": {}, "sources": ["[[strength]]"],
        }],
        "gates": [{
            "id": "w36-renfo-a-reveil", "due": today, "when": "avant",
            "question": "Comment est le injury au réveil ?",
            "on_fail": "Retirer la séance.", "holds": ["w36-renfo-a"],
        }],
    })
    client = MagicMock()
    from redline.messenger import DeliveryResult
    client.send_text.return_value = DeliveryResult.success()
    bot = make_bot(client=client)
    bot._repo.validate_weekly_plan.return_value = PlanStatus.AWAITING
    bot._repo.weekly_plan_context.return_value = context

    assert bot._run_command("4242", "/renfo", message_ids=("mid-held",))

    message = client.send_text.call_args.args[1]
    assert "Le renfo du jour est prévu" in message
    assert "contrôle" not in message
    assert "j'attends d'abord ta réponse" in message
