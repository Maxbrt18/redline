"""Exécute Codex via le SDK Python officiel et ses objets typés."""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import time
from types import SimpleNamespace
from typing import Any

from .agent import (
    HOLDING_MAX_CHARS,
    PLAN_SCOPES,
    PROACTIVE_REPLY_SCHEMA,
    ActiveRuns,
    AgentActivity,
    FailureKind,
    RunResult,
    RunSpec,
    SessionMode,
    Triage,
    live_session,
    structured_reply,
)
from .config import Config, sandbox_overrides
from .prompts import (
    STYLE_PROMPT,
    TRIAGE_STRUCTURED_PROMPT,
    instruction_digest,
    style_prompt,
)
from .routing import Route
from .state import StateStore

log = logging.getLogger("redline")

# Le triage ne lit aucun fichier et n'appelle aucune CLI. Lui retirer le shell évite
# qu'un texte utilisateur ambigu puisse réveiller un outil dont ce tour n'a pas besoin.
TRIAGE_NO_SHELL_OVERRIDE = "features.shell_tool=false"
# Codex ne charge nativement que `AGENTS.md`. La constitution du coach vit dans
# `CLAUDE.md` : l'option est versionnée ici, pas laissée à un `~/.codex/config.toml`
# que la mise à jour nocturne du runtime peut recréer sans elle.
CONSTITUTION_OVERRIDE = 'project_doc_fallback_filenames=["CLAUDE.md"]'
# Un tour ponctuel (triage, banc d'évaluation) ne paie pas la constitution entière.
NO_PROJECT_DOC_OVERRIDE = "project_doc_max_bytes=0"

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "depth": {"type": "string", "enum": ["N0", "N1", "N2", "N3"]},
        "emotional": {"type": "boolean"},
        "holding": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "plan_change": {"type": "boolean"},
        "plan_asked": {"type": "boolean"},
        "plan_scope": {"type": "string", "enum": sorted(PLAN_SCOPES)},
    },
    "required": [
        "depth", "emotional", "holding", "plan_change", "plan_asked", "plan_scope"
    ],
    "additionalProperties": False,
}


class _WatchdogError(RuntimeError):
    pass


class _CancelledError(RuntimeError):
    pass


class _InFlightError(RuntimeError):
    pass


def _load_sdk() -> Any:
    """Charge le SDK seulement si Codex est réellement sélectionné."""
    openai_codex = importlib.import_module("openai_codex")
    sdk_types = importlib.import_module("openai_codex.types")

    return SimpleNamespace(
        Codex=openai_codex.Codex,
        CodexConfig=openai_codex.CodexConfig,
        ApprovalMode=openai_codex.ApprovalMode,
        Sandbox=openai_codex.Sandbox,
        ReasoningEffort=sdk_types.ReasoningEffort,
        CodexError=openai_codex.CodexError,
        is_retryable_error=openai_codex.is_retryable_error,
    )


class CodexRunner:
    """Adaptateur du SDK officiel vers le contrat minimal du bot."""

    provider = "codex"
    MAX_ATTEMPTS = 3
    RETRY_DELAY = 5

    def __init__(self, config: Config, state: StateStore, *, sdk: Any = None) -> None:
        self._config = config
        self._state = state
        self._sdk = sdk
        # Processus outillé du tour en cours : `REDLINE_TURN_ID` est lié à la durée de
        # vie de `codex app-server`, donc un tour = un processus.
        self._client: Any = None
        self._client_turn_id: str | None = None
        self._client_shell_enabled = True
        # Processus permanent sans shell ni document de projet : le triage de chaque
        # message le réutilise au lieu de relancer un `app-server`.
        self._one_shot_client: Any = None
        self._runs = ActiveRuns("Codex")

    def _start(self, overrides: tuple[str, ...], turn_id: str | None) -> Any:
        self._sdk = self._sdk or _load_sdk()
        runtime = self._sdk.CodexConfig(
            codex_bin=self._config.codex_bin,
            cwd=self._config.vault_path,
            config_overrides=overrides,
            # `env` est fusionné PAR-DESSUS os.environ par le SDK : il sert à écraser,
            # pas à filtrer. Les secrets dont l'agent n'a pas besoin sont donc vidés,
            # sinon Codex les recopie dans ses instantanés de shell, en clair.
            env={
                "PATH": os.environ.get("PATH", ""),
                **sandbox_overrides(),
                "REDLINE_TURN_ID": turn_id or "",
            },
        )
        return self._sdk.Codex(config=runtime)

    def _codex(self, turn_id: str | None = None, *, shell_enabled: bool = True) -> Any:
        if (self._client is not None
                and self._client_turn_id == turn_id
                and self._client_shell_enabled == shell_enabled):
            return self._client
        self._close_turn_client()
        overrides = (CONSTITUTION_OVERRIDE,) + (
            () if shell_enabled else (TRIAGE_NO_SHELL_OVERRIDE,))
        self._client = self._start(overrides, turn_id)
        self._client_turn_id = turn_id
        self._client_shell_enabled = shell_enabled
        return self._client

    def _one_shot_codex(self) -> Any:
        if self._one_shot_client is None:
            self._one_shot_client = self._start(
                (TRIAGE_NO_SHELL_OVERRIDE, NO_PROJECT_DOC_OVERRIDE), None)
        return self._one_shot_client

    def _close_turn_client(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._client_turn_id = None
            self._client_shell_enabled = True

    def _close_one_shot_client(self) -> None:
        if self._one_shot_client is not None:
            self._one_shot_client.close()
            self._one_shot_client = None

    def close(self) -> None:
        self._close_turn_client()
        self._close_one_shot_client()

    def _is_retryable_error(self, exc: BaseException) -> bool:
        """Ask the SDK whether an exception is a safe preflight retry.

        The Codex SDK raises overload errors from ``handle.run()`` as well as from
        thread creation.  Keep this provider-specific classification in one place;
        wrapping an overload in ``_InFlightError`` would erase the information that
        the route fallback needs.
        """
        checker = getattr(self._sdk, "is_retryable_error", None)
        if checker is None:
            return False
        try:
            return bool(checker(exc))
        except Exception:  # a broken classifier must not weaken fail-closed writes
            log.exception("Classification de panne Codex indisponible")
            return False

    def activity(self, psid: str) -> AgentActivity | None:
        return self._runs.activity(psid)

    def cancel(self, psid: str) -> bool:
        return self._runs.cancel(psid)

    def _thread_options(self, model: str | None, *,
                        instructions: str | None = STYLE_PROMPT) -> dict:
        return {
            "approval_mode": self._sdk.ApprovalMode.deny_all,
            "cwd": self._config.vault_path,
            # Le style reste obligatoire pour une analyse de pièce jointe en lecture
            # seule. Le classifieur, lui, reçoit uniquement son schéma de triage.
            "developer_instructions": instructions,
            "model": model,
            # Redline est un service applicatif, pas un poste de développement :
            # son isolation est portée par l'utilisateur Unix, l'environnement
            # purgé, la CLI root-owned et la transaction du tour. Le sandbox Linux
            # Codex/bubblewrap ajoutait ici une dépendance d'hôte qui empêchait toute
            # commande de démarrer. ``full_access`` signifie qu'aucun sandbox Codex
            # n'est monté ; les approbations interactives restent refusées.
            "sandbox": self._sdk.Sandbox.full_access,
        }

    def _open_thread(self, psid: str, model: str | None, mode: SessionMode,
                     turn_id: str | None, *, shell_enabled: bool = True) -> Any:
        codex = self._codex(turn_id, shell_enabled=shell_enabled)
        source = None if mode is SessionMode.ISOLATED else live_session(self._state, self._config, self.provider, psid)
        options = self._thread_options(model, instructions=style_prompt(turn_id))
        if mode is SessionMode.FORK and source:
            # A paginated persisted thread can only be forked ephemerally when the
            # response omits its historical turns. The SDK maps this to
            # ``excludeTurns: true``; model context remains available to the fork.
            return codex.thread_fork(
                source, ephemeral=True, include_turns=False, **options)
        if mode is SessionMode.CONTINUE and source:
            return codex.thread_resume(source, **options)
        return codex.thread_start(ephemeral=mode is not SessionMode.CONTINUE, **options)

    def _turn(self, psid: str, thread: Any, message: str, *, model: str | None,
              effort: str | None,
              timeout: int, output_schema: dict | None = None) -> Any:
        options = {
            "approval_mode": self._sdk.ApprovalMode.deny_all,
            "cwd": self._config.vault_path,
            "effort": self._sdk.ReasoningEffort(effort) if effort else None,
            "output_schema": output_schema,
            # Répéter la politique au niveau du tour empêche un preset ou une reprise
            # de thread plus ancien de réintroduire bubblewrap.
            "sandbox": self._sdk.Sandbox.full_access,
        }
        handle = thread.turn(message, **options)
        timed_out = threading.Event()
        active = self._runs.start(
            psid, AgentActivity(model, effort, time.monotonic(), timeout),
            lambda: handle.interrupt())
        cancelled = active.cancelled

        def interrupt() -> None:
            timed_out.set()
            try:
                handle.interrupt()
            except Exception as exc:  # le résultat du tour reste l'autorité finale
                log.warning("Interruption Codex non confirmée : %s", exc)

        timer = threading.Timer(timeout, interrupt)
        timer.start()
        try:
            try:
                result = handle.run()
            except Exception as exc:
                if cancelled.is_set():
                    raise _CancelledError("tour Codex annulé par Sam") from exc
                if timed_out.is_set():
                    raise _WatchdogError(
                        "tour Codex interrompu après le délai maximal") from exc
                # ``ServerBusyError`` means that the provider could not allocate
                # the selected model.  It is a preflight failure even though the
                # native handle already exists, so preserve the SDK exception for
                # the bounded same-model retry and then the route fallback.
                if self._is_retryable_error(exc):
                    raise
                raise _InFlightError(str(exc)) from exc
        finally:
            timer.cancel()
            self._runs.finish(psid, active)
        if cancelled.is_set():
            raise _CancelledError("tour Codex annulé par Sam")
        if timed_out.is_set():
            raise _WatchdogError("tour Codex interrompu après le délai maximal")
        return result

    def run(self, psid: str, message: str, *, spec: RunSpec | None = None,
            turn_id: str | None = None) -> RunResult:
        spec = spec or self._config.routing.plan(Route.N1).primary
        session_mode = spec.session_mode
        last_error = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                thread = self._open_thread(
                    psid, spec.model, session_mode, turn_id,
                    shell_enabled=spec.shell_enabled)
                result = self._turn(
                    psid, thread, message, model=spec.model, effort=spec.effort,
                    timeout=spec.timeout,
                    output_schema=PROACTIVE_REPLY_SCHEMA if spec.structured_reply else None)
                response = result.final_response or ""
                silent = False
                if spec.structured_reply:
                    try:
                        send, response = structured_reply(response)
                    except ValueError as exc:
                        raise _InFlightError(str(exc)) from exc
                    silent = not send
                if session_mode is SessionMode.CONTINUE:
                    self._state.set_session(psid, self.provider, thread.id,
                                            instruction_digest(self._config.vault_path))
                return RunResult(session_id=thread.id, full_text=response, silent=silent)
            except _CancelledError:
                if session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self.provider)
                return RunResult(
                    error="tour Codex annulé par /cancel", failure=FailureKind.CANCELLED,
                    work_started=True)
            except _WatchdogError:
                if session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self.provider)
                return RunResult(
                    error=f"watchdog Codex après {spec.timeout} s",
                    failure=FailureKind.WATCHDOG, work_started=True)
            except _InFlightError as exc:
                if session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self.provider)
                log.warning("Codex failed after work started: %s", exc)
                return RunResult(
                    error=f"panne Codex en cours de tour : {exc}"[:500],
                    failure=FailureKind.IN_FLIGHT, work_started=True)
            except ImportError as exc:
                log.error("SDK Codex absent : %s", exc)
                return RunResult(error="SDK openai-codex absent",
                                 failure=FailureKind.PREFLIGHT)
            except Exception as exc:
                retryable = self._is_retryable_error(exc)
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("Codex failed (attempt %d/%d, retryable=%s): %s",
                            attempt, self.MAX_ATTEMPTS, retryable, exc)
                if not retryable or attempt == self.MAX_ATTEMPTS:
                    break
                time.sleep(self.RETRY_DELAY * attempt)

        if session_mode is SessionMode.CONTINUE:
            self._state.clear_session(psid, self.provider)
        return RunResult(
            error=f"Codex indisponible avant tout travail : {last_error}"[:500],
            failure=FailureKind.PREFLIGHT)

    def one_shot(self, message: str, *, spec: RunSpec, output_schema: dict | None = None,
                 instructions: str | None = None, label: str = "__one_shot__") -> str:
        """Un tour isolé, sans shell, sans session ni constitution.

        Sert au triage de chaque message, sur un processus réutilisé, et au banc
        d'évaluation hors production. Lève l'exception du SDK en cas d'échec.
        """
        codex = self._one_shot_codex()
        try:
            thread = codex.thread_start(
                ephemeral=True, **self._thread_options(spec.model, instructions=instructions))
            result = self._turn(
                label, thread, message, model=spec.model, effort=spec.effort,
                timeout=spec.timeout, output_schema=output_schema)
        except (_WatchdogError, _CancelledError):
            raise
        except Exception:
            # Un processus mort ne se réutilise pas : le prochain appel le relance.
            self._close_one_shot_client()
            raise
        return result.final_response or ""

    def classify(self, message: str, *, spec: RunSpec | None = None) -> Triage:
        """Un seul classifieur rapide ; son absence retombe sur le défaut déterministe."""
        if not self._config.triage_enabled:
            return Triage()
        candidate = spec or self._config.routing.plan(Route.TRIAGE).primary
        try:
            raw = self.one_shot(TRIAGE_STRUCTURED_PROMPT + message, spec=candidate,
                                output_schema=TRIAGE_SCHEMA, label="__triage__")
            return _parse_triage(raw)
        except (_WatchdogError, _CancelledError):
            log.warning("Triage Codex expiré après %d s ; repli sur N1", candidate.timeout)
        except Exception as exc:
            log.warning("Triage Codex indisponible (%s) ; repli sur N1",
                        type(exc).__name__)
        return Triage()


def _parse_triage(raw: str) -> Triage:
    data = json.loads(raw or "{}")
    depth = data["depth"]
    holding = data["holding"]
    if depth not in {"N0", "N1", "N2", "N3"}:
        raise ValueError(f"niveau de triage invalide : {depth!r}")
    if holding is not None and not isinstance(holding, str):
        raise TypeError("holding doit être une chaîne ou null")
    if not all(isinstance(data[flag], bool)
               for flag in ("emotional", "plan_change", "plan_asked")):
        raise TypeError("les indicateurs de triage doivent être booléens")
    plan_scope = data["plan_scope"]
    if plan_scope not in PLAN_SCOPES:
        raise ValueError(f"portée de programme invalide : {plan_scope!r}")
    if bool(data["plan_asked"]) != (plan_scope != "none"):
        raise ValueError("plan_asked et plan_scope se contredisent")
    if holding and len(holding) > HOLDING_MAX_CHARS:
        holding = None
    return Triage(
        depth=depth,
        emotional=bool(data["emotional"]),
        holding=holding or None,
        plan_change=bool(data["plan_change"]),
        plan_asked=bool(data["plan_asked"]),
        plan_scope=plan_scope,
    )
