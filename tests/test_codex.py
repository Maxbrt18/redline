"""Adaptateur Codex : SDK typé, sessions natives et aucun parseur de flux CLI."""

import json
from dataclasses import replace
from enum import Enum
from types import SimpleNamespace

from redline.service.agent import FailureKind, RunSpec, SessionMode, Triage
from redline.service.codex import (
    CONSTITUTION_OVERRIDE,
    NO_PROJECT_DOC_OVERRIDE,
    TRIAGE_NO_SHELL_OVERRIDE,
    CodexRunner,
)
from redline.service.prompts import instruction_digest
from redline.service.routing import Route


class _ApprovalMode(Enum):
    deny_all = "deny_all"


class _Sandbox(Enum):
    read_only = "read-only"
    workspace_write = "workspace-write"
    full_access = "full-access"


class _Handle:
    def __init__(self, response="réponse", fail_after_interrupt=False, run_error=None):
        self.response = response
        self.fail_after_interrupt = fail_after_interrupt
        self.run_error = run_error
        self.interrupted = False

    def run(self):
        if self.interrupted and self.fail_after_interrupt:
            raise RuntimeError("tour interrompu")
        if self.run_error is not None:
            raise self.run_error
        return SimpleNamespace(final_response=self.response)

    def interrupt(self):
        self.interrupted = True


class _Thread:
    def __init__(self, thread_id, response="réponse", run_error=None):
        self.id = thread_id
        self.response = response
        self.run_error = run_error
        self.turn_calls = []
        self.handles = []

    def turn(self, message, **kwargs):
        self.turn_calls.append((message, kwargs))
        handle = _Handle(self.response, run_error=self.run_error)
        self.handles.append(handle)
        return handle


class _Client:
    def __init__(self, response="réponse", run_errors=None):
        self.response = response
        self.run_errors = list(run_errors or [])
        self.calls = []
        self.threads = []
        self.closed = False

    def _thread(self, thread_id):
        error = self.run_errors.pop(0) if self.run_errors else None
        thread = _Thread(thread_id, self.response, run_error=error)
        self.threads.append(thread)
        return thread

    def thread_start(self, **kwargs):
        self.calls.append(("start", None, kwargs))
        return self._thread(f"thread-{len(self.threads) + 1}")

    def thread_resume(self, thread_id, **kwargs):
        self.calls.append(("resume", thread_id, kwargs))
        return self._thread(thread_id)

    def thread_fork(self, thread_id, **kwargs):
        self.calls.append(("fork", thread_id, kwargs))
        return self._thread("thread-fork")

    def close(self):
        self.closed = True


class _SDK:
    ApprovalMode = _ApprovalMode
    Sandbox = _Sandbox
    CodexError = RuntimeError

    def __init__(self, client):
        self.client = client
        self.configs = []

    def CodexConfig(self, **kwargs):
        self.configs.append(kwargs)
        return kwargs

    def Codex(self, config=None):
        return self.client

    @staticmethod
    def ReasoningEffort(value):
        return value

    @staticmethod
    def is_retryable_error(_error):
        return False


def _runner(make_config, store, response="réponse", turn_errors=None, **config):
    client = _Client(response, run_errors=turn_errors)
    sdk = _SDK(client)
    runner = CodexRunner(make_config(agent_provider="codex", **config), store, sdk=sdk)
    return runner, client, sdk


def _spec(runner, route=Route.N1, **overrides) -> RunSpec:
    return replace(runner._config.routing.plan(route).primary, **overrides)


def test_a_first_turn_uses_the_sdk_and_persists_its_native_thread(make_config, store):
    runner, client, _ = _runner(make_config, store)

    result = runner.run("4242", "salut")

    kind, _, options = client.calls[0]
    assert kind == "start"
    assert options["model"] == "gpt-6-luna"
    assert options["sandbox"] is _Sandbox.full_access
    assert options["approval_mode"] is _ApprovalMode.deny_all
    assert "droit de contredire" in options["developer_instructions"]
    assert store.session_id("4242", "codex") == "thread-1"
    assert result.full_text == "réponse"


def test_each_writable_codex_process_receives_its_turn_id(make_config, store):
    runner, _, sdk = _runner(make_config, store)

    runner.run("4242", "salut", turn_id="turn-42")

    assert sdk.configs[-1]["env"]["REDLINE_TURN_ID"] == "turn-42"


def test_production_turns_do_not_start_a_codex_sandbox(make_config, store):
    """Aucun namespace bubblewrap ne se trouve sur le chemin des CLI métier."""
    runner, client, sdk = _runner(make_config, store)

    runner.run("4242", "lis Strava", turn_id="turn-42")

    assert sdk.configs[-1]["config_overrides"] == (CONSTITUTION_OVERRIDE,)
    assert client.calls[0][2]["sandbox"] is _Sandbox.full_access
    _, turn_options = client.threads[0].turn_calls[0]
    assert turn_options["sandbox"] is _Sandbox.full_access


def test_triage_process_cannot_keep_the_next_turns_environment(make_config, store):
    payload = json.dumps({
        "depth": "N1", "emotional": False, "holding": None,
        "plan_change": False, "plan_asked": False, "plan_scope": "none",
    })
    runner, _, sdk = _runner(make_config, store, response=payload)
    runner.classify("salut")

    runner.run("4242", "salut", turn_id="turn-43")

    assert len(sdk.configs) == 2
    assert sdk.configs[0]["env"]["REDLINE_TURN_ID"] == ""
    assert sdk.configs[1]["env"]["REDLINE_TURN_ID"] == "turn-43"


def test_a_live_codex_thread_is_resumed_natively(make_config, store):
    store.set_session("4242", "codex", "thread-live", instruction_digest("."))
    runner, client, _ = _runner(make_config, store)

    runner.run("4242", "suite")

    assert client.calls[0][:2] == ("resume", "thread-live")


def test_a_thread_opened_under_other_instructions_is_never_resumed(make_config, store):
    """⭐ Une session est dérivée de son prompt : quand il change, elle ne vaut plus.

    Le 2026-08-16, la consigne de signature est corrigée à 9 h 08, la prod tire et
    redémarre dans la seconde, et le fil ouvert à 7 h 45 continue — avec, en contexte,
    une vingtaine de messages signés « — Redline ». Le SDK réinjecte pourtant les
    instructions neuves à chaque reprise : ce ne sont pas elles qui manquaient, ce sont
    les EXEMPLES qui restaient. Un modèle imite d'abord ses propres tours.

    Le TTL ne rattrapait rien : il est glissant, donc une conversation vivante ne
    repart jamais d'elle-même. Un correctif de comportement n'atterrissait qu'après
    six heures de silence de Sam, ou un `/clear` qu'il fallait penser à taper.
    """
    store.set_session("4242", "codex", "thread-live", "empreinte-d-avant")
    runner, client, _ = _runner(make_config, store)

    runner.run("4242", "suite")

    assert client.calls[0][0] == "start", "un fil aux vieilles instructions ne reprend pas"
    assert store.session_prompt("4242", "codex") == instruction_digest(".")


def test_a_thread_predating_the_digest_column_is_replaced_too(make_config, store):
    """Empreinte absente = empreinte périmée : les sessions d'avant la colonne
    repartent de zéro plutôt que de survivre à une règle écrite après elles."""
    store.set_session("4242", "codex", "thread-live")
    runner, client, _ = _runner(make_config, store)

    runner.run("4242", "suite")

    assert client.calls[0][0] == "start"


def test_deep_forks_natively_without_replacing_the_interactive_thread(make_config, store):
    store.set_session("4242", "codex", "thread-live", instruction_digest("."))
    runner, client, _ = _runner(make_config, store)

    result = runner.run("4242", "creuse", spec=_spec(runner, Route.DEEP))

    assert client.calls[0][:2] == ("fork", "thread-live")
    assert client.calls[0][2]["ephemeral"] is True
    assert client.calls[0][2]["include_turns"] is False
    assert result.session_id == "thread-fork"
    assert store.session_id("4242", "codex") == "thread-live"


def test_an_isolated_turn_is_ephemeral_and_never_resumed(make_config, store):
    store.set_session("4242", "codex", "thread-live", instruction_digest("."))
    runner, client, _ = _runner(make_config, store)

    runner.run("4242", "bilan", spec=_spec(runner, Route.HEAVY))

    assert client.calls[0][0] == "start"
    assert client.calls[0][2]["ephemeral"] is True
    assert store.session_id("4242", "codex") == "thread-live"


def test_sdk_capacity_error_from_handle_run_remains_fallbackable(make_config, store):
    """Une saturation du modèle n'est pas du travail utile déjà engagé.

    Le SDK expose cette panne comme une erreur retryable, mais elle arrive depuis
    ``handle.run`` après la création du handle. Le transport ne doit pas la
    transformer en ``IN_FLIGHT`` et interdire le repli Luna.
    """
    class CapacityError(RuntimeError):
        pass

    runner, client, sdk = _runner(
        make_config, store,
        turn_errors=[CapacityError("Selected model is at capacity") for _ in range(3)],
    )
    sdk.is_retryable_error = lambda error: isinstance(error, CapacityError)

    result = runner.run("4242", "question", spec=_spec(runner, Route.N2))

    assert len(client.threads) == 3
    assert result.failure is FailureKind.PREFLIGHT
    assert result.work_started is False
    assert result.allows_fallback is True


def test_unclassified_handle_error_remains_terminal(make_config, store):
    class BrokenTurn(RuntimeError):
        pass

    runner, client, sdk = _runner(
        make_config, store,
        turn_errors=[BrokenTurn("connexion interrompue") for _ in range(3)],
    )
    sdk.is_retryable_error = lambda _error: False

    result = runner.run("4242", "question", spec=_spec(runner, Route.N2))

    assert len(client.threads) == 1
    assert result.failure is FailureKind.IN_FLIGHT
    assert result.work_started is True
    assert result.allows_fallback is False


def test_triage_uses_a_structured_output_schema_without_a_shell(make_config, store):
    payload = json.dumps({
        "depth": "N2", "emotional": True, "holding": "Je regarde ça",
        "plan_change": False, "plan_asked": False, "plan_scope": "none",
    })
    runner, client, sdk = _runner(make_config, store, response=payload)

    triage = runner.classify("j'ai mal")

    assert triage == Triage("N2", True, "Je regarde ça", False, False)
    assert sdk.configs[0]["config_overrides"] == (
        TRIAGE_NO_SHELL_OVERRIDE, NO_PROJECT_DOC_OVERRIDE)
    assert client.calls[0][2]["sandbox"] is _Sandbox.full_access
    assert client.calls[0][2]["developer_instructions"] is None
    _, turn_options = client.threads[0].turn_calls[0]
    assert turn_options["output_schema"]["additionalProperties"] is False
    assert turn_options["sandbox"] is _Sandbox.full_access


def test_triage_timeout_falls_back_to_the_default_without_a_second_profile(
        make_config, store, monkeypatch):
    from redline.service.codex import _WatchdogError

    runner, _, _ = _runner(make_config, store)
    calls = []

    def turn(*_args, **_kwargs):
        calls.append(1)
        raise _WatchdogError("profil principal expiré")

    monkeypatch.setattr(runner, "_turn", turn)

    assert runner.classify("je change la séance") == Triage()
    assert len(calls) == 1


def test_one_codex_process_serves_every_triage_and_one_serves_each_turn(make_config, store):
    """Deux messages : le processus du triage est réutilisé, un processus outillé par
    tour. Avant, triage, tour et réécriture relançaient chacun `codex app-server`."""
    payload = json.dumps({
        "depth": "N1", "emotional": False, "holding": None,
        "plan_change": False, "plan_asked": False, "plan_scope": "none",
    })
    runner, _, sdk = _runner(make_config, store, response=payload)

    for turn_id in ("turn-1", "turn-2"):
        runner.classify("salut")
        runner.run("4242", "salut", turn_id=turn_id)

    overrides = [config["config_overrides"] for config in sdk.configs]
    assert overrides.count((TRIAGE_NO_SHELL_OVERRIDE, NO_PROJECT_DOC_OVERRIDE)) == 1
    assert overrides.count((CONSTITUTION_OVERRIDE,)) == 2
    assert len(sdk.configs) == 3


def test_the_constitution_option_is_the_one_codex_understands():
    """Vérifié avec `codex debug prompt-input` (0.157.0) : sans cette option, un runtime
    neuf ne charge pas `CLAUDE.md` ; avec elle, il le charge en entier."""
    assert CONSTITUTION_OVERRIDE == 'project_doc_fallback_filenames=["CLAUDE.md"]'
    assert NO_PROJECT_DOC_OVERRIDE == "project_doc_max_bytes=0"


def test_a_structured_initiative_returns_its_typed_decision(make_config, store):
    runner, client, _ = _runner(
        make_config, store, response='{"send": false, "text": ""}')

    result = runner.run("4242", "mot du matin", spec=_spec(
        runner, session_mode=SessionMode.FORK, structured_reply=True))

    assert result.silent and result.full_text == ""
    _, turn_options = client.threads[0].turn_calls[0]
    assert set(turn_options["output_schema"]["required"]) == {"send", "text"}


def test_a_structured_initiative_outside_its_contract_is_an_in_flight_failure(
        make_config, store):
    runner, _, _ = _runner(make_config, store, response="SILENCE")

    result = runner.run("4242", "mot du matin", spec=_spec(
        runner, session_mode=SessionMode.FORK, structured_reply=True))

    assert result.failure is FailureKind.IN_FLIGHT and not result.allows_fallback


def test_failures_carry_technical_detail_never_a_sentence_for_alex(make_config, store):
    class BrokenTurn(RuntimeError):
        pass

    runner, _, sdk = _runner(make_config, store,
                             turn_errors=[BrokenTurn("socket fermée") for _ in range(3)])
    sdk.is_retryable_error = lambda _error: False

    result = runner.run("4242", "question", spec=_spec(runner, Route.N2))

    assert result.error.startswith("panne Codex en cours de tour")
    assert "Réessaie" not in result.error and "Il ne sera pas" not in result.error


def test_attachment_turn_keeps_the_coaching_session_without_codex_sandbox(
        make_config, store):
    store.set_session("4242", "codex", "thread-live", instruction_digest("."))
    runner, client, _ = _runner(make_config, store)

    runner.run("4242", "analyse ce PDF", spec=_spec(runner, read_only=True))

    assert client.calls[0][:2] == ("resume", "thread-live")
    assert client.calls[0][2]["sandbox"] is _Sandbox.full_access
    assert "droit de contredire" in client.calls[0][2]["developer_instructions"]
    _, turn_options = client.threads[0].turn_calls[0]
    assert turn_options["sandbox"] is _Sandbox.full_access


def test_timeout_interrupts_the_native_turn_handle(make_config, store, monkeypatch):
    class ImmediateTimer:
        def __init__(self, _timeout, callback):
            self.callback = callback

        def start(self):
            self.callback()

        def cancel(self):
            pass

    monkeypatch.setattr("redline.service.codex.threading.Timer", ImmediateTimer)
    runner, client, _ = _runner(make_config, store)

    result = runner.run("4242", "long", spec=_spec(runner, timeout=1))

    assert client.threads[0].handles[0].interrupted is True
    assert "watchdog" in result.error
    assert result.work_started is True
    assert result.allows_fallback is False
    assert store.session_id("4242", "codex") is None


def test_timeout_stays_a_timeout_when_the_sdk_reports_an_interrupted_turn(
        make_config, store, monkeypatch):
    class ImmediateTimer:
        def __init__(self, _timeout, callback):
            self.callback = callback

        def start(self):
            self.callback()

        def cancel(self):
            pass

    monkeypatch.setattr("redline.service.codex.threading.Timer", ImmediateTimer)
    runner, client, _ = _runner(make_config, store)
    original_turn = client._thread

    def interrupted_thread(thread_id):
        thread = original_turn(thread_id)

        def turn(message, **kwargs):
            thread.turn_calls.append((message, kwargs))
            handle = _Handle(thread.response, fail_after_interrupt=True)
            thread.handles.append(handle)
            return handle

        thread.turn = turn
        return thread

    client._thread = interrupted_thread

    result = runner.run("4242", "long", spec=_spec(runner, timeout=1))

    assert "watchdog" in result.error
    assert result.work_started is True
    assert result.allows_fallback is False


def test_close_releases_the_long_lived_sdk_runtime(make_config, store):
    runner, client, _ = _runner(make_config, store)
    runner.run("4242", "salut")

    runner.close()

    assert client.closed is True


def test_the_service_secrets_are_blanked_before_codex_ever_starts(make_config, store,
                                                                  monkeypatch):
    """Codex recopie l'environnement du service dans ses instantanés de shell, en
    clair et sur le disque (2026-08-14 : neuf fichiers, jeton de page Messenger
    compris). Le filtre doit donc s'appliquer à la CONSTRUCTION du client, avant que
    le binaire ne démarre — le neutraliser après coup n'effacerait pas les copies
    déjà écrites."""
    monkeypatch.setenv("MESSENGER_PAGE_TOKEN", "EAAG-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@/redline")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "strava-secret")

    runner, _, sdk = _runner(make_config, store)
    runner.run("4242", "salut")

    env = sdk.configs[0]["env"]
    assert env["MESSENGER_PAGE_TOKEN"] == ""
    # Ce dont le bac à sable a besoin n'est jamais écrasé : il hérite de la vraie
    # valeur par la copie que fait le SDK, donc rien à surcharger ici.
    assert "DATABASE_URL" not in env
    assert "STRAVA_CLIENT_SECRET" not in env
    assert env["PATH"]


def test_the_one_shot_process_is_restarted_after_a_crash(make_config, store, monkeypatch):
    runner, client, _ = _runner(make_config, store)

    def crash(*_args, **_kwargs):
        raise RuntimeError("processus mort")

    monkeypatch.setattr(runner, "_turn", crash)
    assert runner.classify("salut") == Triage()
    assert client.closed is True
    assert runner._one_shot_client is None
