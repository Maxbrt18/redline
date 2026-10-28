"""Non-régression de l'audit du workflow Messenger (audit/redline-workflow-audit.md).

Chaque constat reproduit par l'audit a changé de sens : ces tests décrivent ce que
Sam observe désormais. Ils comptent des effets (appels de modèle, messages envoyés,
acquittements, jobs), jamais des formulations de prompt.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import subprocess
import time
from unittest.mock import MagicMock

from conftest import ENGINE_ROOT, FakeAgent, git

from redline.agent import FailureKind, RunResult, SessionMode
from redline.bot import (
    ATTACHMENT_UNREADABLE,
    FAILURE_REPLIES,
    OUTAGE_NOTICE,
    PLAN_UNAVAILABLE_LINE,
    CoachBot,
)
from redline.messenger import DeliveryResult
from redline.plans import PlanStatus
from redline.proactive import ProactiveJob
from redline.routing import Route
from redline.vault import PersistenceResult, VaultRepo
from redline.work_queue import AttachmentRequest, InboundRequest

PSID = "4242"


class ScriptedAgent(FakeAgent):
    """FakeAgent dont chaque appel `run` consomme le résultat suivant d'une liste."""

    def __init__(self, results, **triage):
        super().__init__(**triage)
        self._results = list(results)

    def run(self, psid, message, *, spec, turn_id=None):
        super().run(psid, message, spec=spec, turn_id=turn_id)
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


def _sent(client) -> str:
    calls = client.send_burst.call_args_list + client.send_text.call_args_list
    return " ".join(str(call.args[1]) for call in calls)


def _claim(store, key, kind, detail="", *, expire_at=0.0):
    store.proactive.enqueue_job(key, kind, detail, time.time() - 1, expire_at=expire_at)
    record = store.proactive.claim_due_job(time.time())
    return ProactiveJob(psid=PSID, kind=kind, prompt="p", job_id=record["id"],
                        detail=record["detail"], expire_at=record["expire_at"] or None,
                        attempts=record["attempts"])


# --------------------------------------------------------------------------- RWF-001


def test_rwf001_an_applied_command_never_costs_a_second_model_call(make_bot):
    agent = FakeAgent(result=RunResult(full_text="Tu me donnes ton RPE ?"))
    bot = make_bot(agent=agent)
    bot._repo.persist_turn.return_value = PersistenceResult(
        True, applied_commands=1,
        receipts=({"command_key": "k", "kind": "question.ask", "payload": {},
                   "applied_at": "2026-09-25T08:00:00+00:00"},))

    assert bot._respond(PSID, "sortie faite", message_ids=("m1",))

    assert len(agent.calls) == 1
    assert agent.calls[0]["session_mode"] is SessionMode.CONTINUE
    assert "Tu me donnes ton RPE ?" in _sent(bot._client)


def test_rwf001_a_finished_answer_is_delivered_as_written(make_bot):
    draft = "Bien reçu, j'ai décalé ta sortie longue à dimanche, garde 10 km max."
    agent = ScriptedAgent([RunResult(full_text=draft),
                           RunResult(error="quota", failure=FailureKind.PREFLIGHT)])
    bot = make_bot(agent=agent)
    bot._repo.persist_turn.return_value = PersistenceResult(True, applied_commands=1)

    assert bot._respond(PSID, "je décale la SL à dimanche", message_ids=("m2",))

    sent = _sent(bot._client)
    assert draft in sent
    assert "ma réponse s'est interrompue" not in sent
    assert len(agent.calls) == 1


# --------------------------------------------------------------------------- RWF-002


def test_rwf002_a_safety_answer_leaves_even_when_the_week_is_unreadable(make_bot):
    safety = ("Arrête la séance maintenant. Une douleur injury focale qui augmente "
              "mérite un avis médical avant de recourir.")
    agent = FakeAgent(result=RunResult(full_text=safety), depth="N2", plan_change=True)
    bot = make_bot(agent=agent)
    bot._repo.weekly_plan_context.return_value = ""
    bot._repo.persist_turn.return_value = PersistenceResult(True, invalid=("2026-W39",))

    assert bot._respond(PSID, "j'ai une douleur nette au injury en courant",
                        message_ids=("m3",))

    sent = _sent(bot._client)
    assert safety in sent
    assert "vérifier le programme" not in sent


def test_rwf002_a_programme_request_without_a_valid_week_gets_one_precise_line(make_bot):
    agent = FakeAgent(result=RunResult(full_text="Je regarde ça."), plan_scope="today")
    bot = make_bot(agent=agent)
    bot._repo.weekly_plan_context.return_value = ""

    assert bot._respond(PSID, "je fais quoi aujourd'hui ?", message_ids=("m3b",))

    assert PLAN_UNAVAILABLE_LINE in _sent(bot._client)
    bot._repo.weekly_plan_message.assert_not_called()


# --------------------------------------------------------------------------- RWF-004


def test_rwf004_no_adapter_text_ever_reaches_alex(make_bot):
    agent = FakeAgent(result=RunResult(error="watchdog Codex après 1800 s",
                                       failure=FailureKind.WATCHDOG, work_started=True))
    bot = make_bot(agent=agent)

    assert bot._respond(PSID, "tu peux regarder ma semaine ?", message_ids=("m4",))

    sent = _sent(bot._client)
    assert sent == FAILURE_REPLIES[FailureKind.WATCHDOG]
    for word in ("watchdog", "tour", "relancé automatiquement", "Codex"):
        assert word not in sent


def test_rwf004_every_failure_reply_speaks_about_alex_not_the_mechanism():
    for text in FAILURE_REPLIES.values():
        for word in ("watchdog", "tour", "contrôle", "moteur", "Codex", "Claude"):
            assert word not in text


def test_rwf004_a_cancelled_turn_sends_nothing_more(make_bot):
    agent = FakeAgent(result=RunResult(error="annulé", failure=FailureKind.CANCELLED,
                                       work_started=True))
    bot = make_bot(agent=agent)

    assert bot._respond(PSID, "long calcul", message_ids=("m4b",))

    assert _sent(bot._client) == ""


def test_rwf004_claude_retries_without_a_dead_session_before_any_work(
        make_config, store, monkeypatch):
    from redline.claude import ClaudeCodeRunner
    from redline.prompts import instruction_digest

    launched = []
    outputs = [
        [json.dumps({"type": "result", "is_error": True,
                     "result": "No conversation found with session ID: sess-old"})],
        [json.dumps({"type": "system", "session_id": "sess-new"}),
         json.dumps({"type": "assistant", "message": {"content": [
             {"type": "text", "text": "Salut !"}]}}),
         json.dumps({"type": "result", "is_error": False, "result": "Salut !"})],
    ]

    class Proc:
        def __init__(self, lines):
            self.stdout = iter(line + "\n" for line in lines)
            self.stderr = iter([])
            self._code = 1 if len(lines) == 1 else 0

        def wait(self):
            return self._code

        def kill(self):  # pragma: no cover
            pass

        def terminate(self):  # pragma: no cover
            pass

    def popen(command, **kwargs):
        launched.append(command)
        return Proc(outputs[len(launched) - 1])

    monkeypatch.setattr("redline.claude.subprocess.Popen", popen)
    monkeypatch.setattr("redline.claude.time.sleep", lambda _seconds: None)
    store.set_session(PSID, "claude", "sess-old", instruction_digest("."))
    runner = ClaudeCodeRunner(make_config(agent_provider="claude"), store)

    result = runner.run(PSID, "salut", spec=runner._config.routing.plan(Route.N1).primary)

    assert result.full_text == "Salut !" and not result.error
    assert "--resume" in launched[0] and "--resume" not in launched[1]
    assert store.session_id(PSID, "claude") == "sess-new"


def test_rwf023_claude_initiatives_return_their_typed_decision(
        make_config, store, monkeypatch):
    from dataclasses import replace

    from redline.claude import ClaudeCodeRunner

    launched = []

    class Proc:
        stdout = iter([
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "délibération"}]}}) + "\n",
            json.dumps({"type": "result", "is_error": False, "result": "",
                        "structured_output": {"send": False, "text": ""}}) + "\n"])
        stderr = iter([])

        def wait(self):
            return 0

        def terminate(self):  # pragma: no cover
            pass

    def popen(command, **kwargs):
        launched.append(command)
        return Proc()

    monkeypatch.setattr("redline.claude.subprocess.Popen", popen)
    runner = ClaudeCodeRunner(make_config(agent_provider="claude"), store)
    spec = replace(runner._config.routing.plan(Route.N1).primary,
                   session_mode=SessionMode.FORK, structured_reply=True)

    result = runner.run(PSID, "mot du matin", spec=spec)

    assert result.silent and result.full_text == ""
    assert "--json-schema" in launched[0]


# --------------------------------------------------------------------------- RWF-005


def test_rwf005_a_reply_delivered_before_a_memory_failure_is_never_recomputed(make_bot):
    agent = ScriptedAgent([RunResult(full_text="Réponse A"), RunResult(full_text="Réponse B")])
    bot = make_bot(agent=agent)
    original = bot._state.record_outbound
    bot._state.record_outbound = MagicMock(side_effect=RuntimeError("PostgreSQL indisponible"))

    assert bot._respond(PSID, "question", message_ids=("m5",))
    bot._state.record_outbound = original
    assert bot._respond(PSID, "question", message_ids=("m5",))

    assert len(agent.calls) == 1
    assert _sent(bot._client).count("Réponse A") == 1
    assert "Réponse B" not in _sent(bot._client)


def test_rwf005_a_transport_failure_resends_the_stored_answer_without_the_model(make_bot):
    agent = ScriptedAgent([RunResult(full_text="Réponse A"), RunResult(full_text="Réponse B")])
    client = MagicMock()
    client.send_burst.return_value = DeliveryResult.failure("Send API indisponible")
    client.send_text.return_value = DeliveryResult.failure("Send API indisponible")
    client.action.return_value = DeliveryResult.success()
    bot = make_bot(agent=agent, client=client)

    assert bot._respond(PSID, "question", message_ids=("m6",)) is False
    client.send_burst.return_value = DeliveryResult.success()
    assert bot._respond(PSID, "question", message_ids=("m6",)) is True

    assert len(agent.calls) == 1
    assert bot._repo.persist_turn.call_count == 1
    assert client.send_burst.call_args.args[1] == "Réponse A"
    assert bot._state._conversation[-1]["text"] == "Réponse A"


# --------------------------------------------------------------------------- RWF-006


def test_rwf006_a_long_outage_is_announced_once_and_never_leaves_silence(make_bot,
                                                                         monkeypatch):
    agent = FakeAgent(result=RunResult(error="saturé", failure=FailureKind.PREFLIGHT))
    bot = make_bot(agent=agent)
    timers = []

    class Timer:
        def __init__(self, delay, callback):
            timers.append(delay)

        def start(self):
            pass

    monkeypatch.setattr("redline.bot.threading.Timer", Timer)

    for attempt in (1, 2, 3, 4):
        bot._retry_inbound(InboundRequest(PSID, "question", ("m7",), attempts=attempt))

    assert timers == [30.0, 300.0, 1800.0, 1800.0]
    assert _sent(bot._client).count(OUTAGE_NOTICE) == 1


def test_rwf006_the_provider_reset_time_paces_the_retry(make_bot, monkeypatch):
    bot = make_bot()
    timers = []

    class Timer:
        def __init__(self, delay, callback):
            timers.append(delay)

        def start(self):
            pass

    monkeypatch.setattr("redline.bot.threading.Timer", Timer)
    bot._retry_not_before = time.time() + 3600

    bot._retry_inbound(InboundRequest(PSID, "question", ("m8",)))

    assert 3590 <= timers[0] <= 3600


def test_rwf006_an_old_message_is_answered_with_its_age(make_bot):
    agent = FakeAgent(result=RunResult(full_text="Désolé pour le délai."))
    bot = make_bot(agent=agent)
    bot._state.claim_inbound("old", PSID, "text", "je fais quoi demain ?")
    bot._state._inbound_messages["old"]["received_at"] = time.time() - 5 * 3600

    bot._respond(PSID, "je fais quoi demain ?", message_ids=("old",))

    prompt = agent.calls[0]["message"]
    assert "[DÉLAI" in prompt and "il y a 5 h" in prompt


def test_b20_a_message_overtaken_by_the_previous_reply_is_flagged(make_bot):
    agent = FakeAgent(result=RunResult(full_text="ok"))
    bot = make_bot(agent=agent)
    bot._state.claim_inbound("during", PSID, "text", "et pour jeudi ?")
    bot._last_reactive_reply = time.time() + 1

    bot._respond(PSID, "et pour jeudi ?", message_ids=("during",))

    assert "partie après lui" in agent.calls[0]["message"]


# --------------------------------------------------------------------------- RWF-007


def test_rwf007_an_invalid_morning_is_not_regenerated(make_bot, store):
    agent = FakeAgent(result=RunResult(full_text="Bonjour !"))
    bot = make_bot(agent=agent, proactive_active_start=None)
    bot._repo.persist_turn.return_value = PersistenceResult(True, invalid=("2026-W39",))
    job = _claim(store, "morning:2026-09-25", "morning",
                 expire_at=time.time() + 3 * 3600)

    bot._respond_proactive(job)

    statuses = {row["id"]: row["status"] for row in store.proactive.jobs()}
    assert statuses["morning:2026-09-25"] == "done"
    assert "Bonjour !" in _sent(bot._client)
    assert len(agent.calls) == 1


def test_rwf007_an_undeliverable_debrief_is_bounded_and_never_regenerated(
        make_bot, store):
    agent = FakeAgent(result=RunResult(full_text="Belle sortie !"))
    client = MagicMock()
    client.send_burst.return_value = DeliveryResult.failure("Send API 400: OAuthException")
    client.send_text.return_value = DeliveryResult.failure("Send API 400: OAuthException")
    client.action.return_value = DeliveryResult.success()
    bot = make_bot(agent=agent, client=client, proactive_active_start=None)

    for _ in range(3):
        job = _claim(store, "strava:987", "strava_debrief", "987")
        bot._respond_proactive(job)
        row = next(row for row in store.proactive.jobs() if row["id"] == "strava:987")
        if row["status"] == "pending":
            assert row["expire_at"]
            store.proactive.retry_job("strava:987", time.time() - 1, row["expire_at"])

    row = next(row for row in store.proactive.jobs() if row["id"] == "strava:987")
    assert row["status"] == "failed"
    assert len(agent.calls) == 1


def test_rwf007_a_released_job_always_carries_an_expiry(make_bot, store):
    bot = make_bot()
    job = _claim(store, "pre-session:1", "pre_session")

    bot._release_job(job)

    row = store.proactive.jobs()[0]
    assert row["status"] == "pending" and row["expire_at"] > time.time()


# --------------------------------------------------------------------------- RWF-009


def test_rwf009_a_week_without_a_valid_plan_gets_one_repair_job(make_bot, store):
    bot = make_bot()
    bot._repo.validate_weekly_plan.return_value = PlanStatus.INVALID
    tuesday = dt.datetime(2026, 9, 22, 8, 0)

    assert bot.materialize_week_repair(tuesday)
    bot._week_repair_checked = 0.0
    assert not bot.materialize_week_repair(tuesday)

    repair = [row for row in store.proactive.jobs() if row["kind"] == "weekly_repair"]
    assert [(row["id"], row["detail"]) for row in repair] == [
        ("weekly-repair:2026-W39", "2026-W39")]


def test_rwf009_no_repair_on_sunday_nor_while_the_review_is_pending(make_bot, store):
    bot = make_bot()
    bot._repo.validate_weekly_plan.return_value = PlanStatus.INVALID
    assert not bot.materialize_week_repair(dt.datetime(2026, 9, 27, 20, 0))

    store.proactive.enqueue_job("weekly:2026-W38", "weekly", "", 0)
    bot._week_repair_checked = 0.0
    assert not bot.materialize_week_repair(dt.datetime(2026, 9, 22, 8, 0))


def test_rwf009_a_valid_week_needs_no_repair(make_bot, store):
    bot = make_bot()
    bot._repo.validate_weekly_plan.return_value = PlanStatus.VALID

    assert not bot.materialize_week_repair(dt.datetime(2026, 9, 22, 8, 0))
    assert store.proactive.jobs() == []


def test_rwf009_the_repair_job_must_leave_a_valid_week_behind(make_bot, store):
    agent = FakeAgent(result=RunResult(full_text="La suite de ta semaine est prête."))
    bot = make_bot(agent=agent, proactive_active_start=None)
    bot._repo.weekly_plan_message.return_value = "Programme 2026-W39 : mer EF"
    job = _claim(store, "weekly-repair:2026-W39", "weekly_repair", "2026-W39")

    bot._respond_proactive(job)

    assert agent.calls[0]["session_mode"] is SessionMode.ISOLATED
    assert bot._repo.persist_turn.call_args.kwargs["required_weeks"] == ("2026-W39",)
    assert "Programme 2026-W39" in _sent(bot._client)


# --------------------------------------------------------------------------- RWF-013


def test_rwf013_the_window_starts_in_the_morning_and_runs_to_midnight(make_config):
    config = make_config(proactive_active_start=dt.time(7, 30))
    assert not config.in_active_window(dt.time(6, 59))
    assert config.in_active_window(dt.time(23, 50))


def test_rwf013_a_recent_reactive_reply_keeps_initiatives_quiet(make_bot):
    bot = make_bot()
    assert not bot.conversation_active(PSID)

    bot._last_reactive_reply = time.time()
    assert bot.conversation_active(PSID)

    bot._last_reactive_reply = 0.0
    bot._reactive_since = time.time()
    assert bot.conversation_active(PSID)


# --------------------------------------------------------------------------- RWF-014


def test_rwf014_the_holding_line_is_a_real_remembered_message(make_bot):
    bot = make_bot()

    bot._deliver_holding(PSID, "Je regarde ta semaine.")

    row = bot._state._conversation[-1]
    assert row["text"] == "Je regarde ta semaine."
    assert row["resolve_questions"] is False
    assert row["source_key"].startswith("holding:")


# --------------------------------------------------------------------------- RWF-016


def test_rwf016_a_turn_is_traceable_by_structured_events_without_text(make_bot, caplog):
    agent = FakeAgent(result=RunResult(full_text="Réponse privée sur le injury"))
    bot = make_bot(agent=agent)

    with caplog.at_level(logging.INFO, logger="redline.events"):
        bot._respond(PSID, "question privée", message_ids=("m9",))

    events = [json.loads(record.getMessage()) for record in caplog.records
              if record.name == "redline.events"]
    stages = [event["stage"] for event in events]
    assert stages == ["triage", "model", "apply", "shape", "deliver"]
    shape = events[3]
    assert shape["prose_bubbles"] == 1 and shape["added_parts"] == 0
    assert shape["prose_chars"] == len("Réponse privée sur le injury")
    turn_ids = {event["turn_id"] for event in events if "turn_id" in event}
    assert len(turn_ids) == 1
    assert all("injury" not in json.dumps(event) and "privée" not in json.dumps(event)
               for event in events)


def test_rwf016_the_release_check_and_the_service_fingerprint_the_same_code(git_repo):
    """`verify-release.sh` et `/health` doivent calculer la même empreinte, et un commit
    de notes du coach ne doit pas la faire bouger."""
    for tree in ("server", "scripts", "migrations"):
        (git_repo / tree).mkdir()
        (git_repo / tree / "code.py").write_text(f"# {tree}\n", encoding="utf-8")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-m", "code")
    script = (ENGINE_ROOT / "server" / "verify-release.sh").read_text(encoding="utf-8")
    pipeline = re.search(r"^trees=.*?^expected_tree=[^\n]*", script, re.DOTALL | re.MULTILINE)
    assert pipeline is not None
    shell = subprocess.run(
        ["bash", "-c", f'{pipeline.group(0)}\nprintf %s "$expected_tree"'],
        env={"PATH": "/usr/bin:/bin", "repo_root": str(git_repo), "expected": "HEAD"},
        capture_output=True, text=True, check=True)
    repo = VaultRepo(str(git_repo), "origin", "main")
    before = repo.runtime_tree()

    (git_repo / "note.md").write_text("une note du coach", encoding="utf-8")
    git(git_repo, "commit", "-am", "coach (messenger)")

    assert shell.stdout == before and len(before) == 12
    assert repo.runtime_tree() == before


def test_rwf016_an_event_cannot_carry_free_text():
    import pytest

    from redline import events

    with pytest.raises(ValueError, match="non autorisé"):
        events.emit("turn.stage", message="texte de Sam")


# --------------------------------------------------------------------------- RWF-021


def test_rwf021_an_attachment_that_failed_in_flight_is_not_reanalysed(make_bot):
    from contextlib import contextmanager

    agent = FakeAgent(result=RunResult(error="watchdog", failure=FailureKind.WATCHDOG,
                                       work_started=True))
    attachments = MagicMock()

    @contextmanager
    def materialize(payload, message_id):
        yield MagicMock(files=(), ignored_video=False)

    attachments.materialize = materialize
    attachments.caption.return_value = ""
    bot = make_bot(agent=agent, attachments=attachments)

    assert bot._respond_attachment(AttachmentRequest(PSID, "{}", "att-1"))

    assert len(agent.calls) == 1
    assert ATTACHMENT_UNREADABLE in _sent(bot._client)


# --------------------------------------------------------------------------- RWF-022


def test_rwf022_an_initiative_shows_no_typing_while_it_is_written(make_bot):
    bot = make_bot(agent=FakeAgent(result=RunResult(full_text="", silent=True)),
                   proactive_active_start=None)

    bot._respond_proactive(ProactiveJob(psid=PSID, kind="daily_signal", prompt="p"))

    assert all(call.args[1] != "typing_on" for call in bot._client.action.call_args_list)


# --------------------------------------------------------------------------- RWF-024


def test_rwf024_a_turn_reads_the_declarative_ledger_once(make_bot):
    bot = make_bot(agent=FakeAgent(result=RunResult(full_text="ok")))

    bot._respond(PSID, "comment s'est passée ma semaine ?", message_ids=("m11",))

    bot._repo.ledger_cells.assert_called_once()
    bot._repo.pending_ledger_cells.assert_not_called()


# --------------------------------------------------------------------------- RWF-025


def test_rwf025_a_closed_initiative_with_an_unreadable_detail_never_wakes_the_agent(
        make_bot, store):
    agent = FakeAgent()
    bot = make_bot(agent=agent, proactive_active_start=None)
    job = _claim(store, "operator:1", "operator_message", "{}")

    bot._respond_proactive(job)

    assert agent.calls == []
    assert store.proactive.jobs()[0]["status"] == "failed"


# --------------------------------------------------------------------------- RWF-026


def test_rwf026_a_read_timeout_is_never_resent(make_config, monkeypatch):
    import requests

    from redline.messenger import MessengerClient

    posts = []

    def post(*args, **kwargs):
        posts.append(kwargs.get("timeout"))
        raise requests.ReadTimeout("pas de réponse")

    monkeypatch.setattr("redline.messenger.requests.post", post)
    result = MessengerClient(make_config()).send_text(PSID, "Bonjour")

    assert len(posts) == 1 and posts[0] == MessengerClient.TIMEOUT
    assert not result.ok and result.delivered == 1


# ----------------------------------------------------------- appels par parcours


def test_every_reactive_route_is_one_model_call(make_bot):
    for depth in ("N0", "N1", "N2", "N3"):
        agent = FakeAgent(result=RunResult(full_text="ok"), depth=depth)
        bot = make_bot(agent=agent)
        bot._respond(PSID, "un message", message_ids=(f"route-{depth}",))
        assert len(agent.calls) == 1, depth
    agent = FakeAgent(result=RunResult(full_text="analyse"))
    bot = make_bot(agent=agent)
    bot._respond(PSID, "creuse", deep_request=True, message_ids=("deep",))
    assert len(agent.calls) == 1


def test_publication_happens_after_delivery_in_the_worker(make_bot):
    class OneItemQueue:
        def __init__(self, item):
            self._items = [item]

        def next(self):
            return self._items.pop() if self._items else None

    bot = make_bot()
    order = []
    bot._repo.persist_turn.side_effect = lambda **_kwargs: (
        order.append("persist") or PersistenceResult(True, applied_commands=1))
    bot._repo.publish.side_effect = lambda: order.append("publish")
    original = bot._delivery.deliver

    def deliver(envelope):
        order.append("deliver")
        return original(envelope)

    bot._delivery.deliver = deliver
    bot._queue = OneItemQueue(InboundRequest(PSID, "salut", ("m10",)))

    bot._worker()

    assert order == ["persist", "deliver", "publish"]
    bot._repo.sync.assert_called_once()


def test_retry_delays_are_declared_where_they_are_used():
    assert CoachBot.PROACTIVE_MAX_ATTEMPTS == 3
    assert CoachBot.REACTIVE_RETRY_DELAYS[0] == 30.0


# --------------------------------------------------------------------------- RWF-017


def test_rwf017_the_documentation_names_no_removed_mechanism():
    """Une doc qui décrit une architecture disparue fait prendre de mauvaises décisions."""
    removed = ("finalize_response", "contrôle de réponse", "commit_and_push_if_dirty",
               "silence_verdict", "response_review", "règles locales",
               "contrôle de cohérence", "rédaction en branche éphémère")
    documents = [*sorted((ENGINE_ROOT / "docs").glob("*.md")),
                 ENGINE_ROOT / "server" / "README.md", ENGINE_ROOT / "server" / ".env.example"]
    stale = [(path.name, name) for path in documents
             for name in removed if name in path.read_text(encoding="utf-8")]
    assert stale == []
