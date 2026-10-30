"""Runs Codex through the official Python SDK and its typed objects."""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import time
from types import SimpleNamespace
from typing import Any

from redline.model import paths

from . import persona
from .agent import (
    PROACTIVE_REPLY_SCHEMA,
    TRIAGE_SCHEMA,
    ActiveRuns,
    AgentActivity,
    FailureKind,
    RunResult,
    RunSpec,
    SessionMode,
    Triage,
    live_session,
    parse_triage,
    structured_reply,
)
from .config import Config, sandbox_overrides
from .prompts import (
    STYLE_PROMPT,
    TRIAGE_PROMPT,
    instruction_digest,
    with_turn_input,
)
from .routing import Route
from .state import StateStore

log = logging.getLogger("redline")

# Triage reads no file and calls no CLI. Removing its shell keeps an ambiguous user
# text from waking a tool this turn does not need.
TRIAGE_NO_SHELL_OVERRIDE = "features.shell_tool=false"
# Codex natively loads only `AGENTS.md`. The coach constitution lives in `CLAUDE.md`:
# the option is versioned here rather than left to a `~/.codex/config.toml` that a
# runtime update can recreate without it.
CONSTITUTION_OVERRIDE = 'project_doc_fallback_filenames=["CLAUDE.md"]'
# A one-off turn (triage, evaluation bench) does not pay for the whole constitution.
NO_PROJECT_DOC_OVERRIDE = "project_doc_max_bytes=0"
# The coach's commands reach Strava and PostgreSQL, and write their cards to the state
# directory: the sandbox keeps the network open and adds that one writable root to the
# vault. Everything else, `.git` and `.codex` included, stays read-only.
NETWORK_OVERRIDE = "sandbox_workspace_write.network_access=true"


def _writable_roots_override() -> str:
    return f"sandbox_workspace_write.writable_roots={json.dumps([str(paths.state_dir())])}"

class _WatchdogError(RuntimeError):
    pass


class _CancelledError(RuntimeError):
    pass


class _InFlightError(RuntimeError):
    pass


def _load_sdk() -> Any:
    """Load the SDK only if Codex is actually selected."""
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
    """Adapter from the official SDK to the bot's minimal contract."""

    provider = "codex"
    MAX_ATTEMPTS = 3
    RETRY_DELAY = 5

    def __init__(self, config: Config, state: StateStore, *, sdk: Any = None) -> None:
        self._config = config
        self._state = state
        self._sdk = sdk
        # Tooled process of the current turn: `REDLINE_TURN_ID` is tied to the lifetime
        # of `codex app-server`, so one turn = one process.
        self._client: Any = None
        self._client_turn_id: str | None = None
        self._client_shell_enabled = True
        # Long-lived process with no shell and no project document: the triage of each
        # message reuses it instead of relaunching an `app-server`.
        self._one_shot_client: Any = None
        self._runs = ActiveRuns("Codex")

    def _start(self, overrides: tuple[str, ...], turn_id: str | None) -> Any:
        self._sdk = self._sdk or _load_sdk()
        runtime = self._sdk.CodexConfig(
            codex_bin=self._config.codex_bin,
            cwd=self._config.vault_path,
            config_overrides=overrides,
            # The SDK merges `env` ON TOP of os.environ: it overrides, it does not
            # filter. Secrets the agent does not need are therefore blanked, otherwise
            # Codex copies them in clear into its shell snapshots.
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
        overrides = (CONSTITUTION_OVERRIDE, NETWORK_OVERRIDE, _writable_roots_override()) + (
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
            log.exception("Codex outage classification unavailable")
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
            # The style stays mandatory for a read-only attachment analysis. The
            # classifier receives only its triage schema.
            "developer_instructions": (None if instructions is None
                                       else self._personal(instructions)),
            "model": model,
            # Commands run in the Codex sandbox (bubblewrap on Linux): they may write
            # the vault and the state directory only. Interactive approvals stay denied.
            "sandbox": self._sdk.Sandbox.workspace_write,
        }

    def _personal(self, text: str) -> str:
        """Substitute the instance athlete name for the token in everything the model reads."""
        return persona.render(text, self._config.instance.athlete_name)

    def _open_thread(self, psid: str, model: str | None, mode: SessionMode,
                     turn_id: str | None, *, shell_enabled: bool = True) -> Any:
        codex = self._codex(turn_id, shell_enabled=shell_enabled)
        source = None if mode is SessionMode.ISOLATED else live_session(self._state, self._config, self.provider, psid)
        options = self._thread_options(model)
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
            # Repeating the policy at turn level keeps a preset or a resumed older
            # thread from widening it.
            "sandbox": self._sdk.Sandbox.workspace_write,
        }
        handle = thread.turn(self._personal(message), **options)
        timed_out = threading.Event()
        active = self._runs.start(
            psid, AgentActivity(model, effort, time.monotonic(), timeout),
            lambda: handle.interrupt())
        cancelled = active.cancelled

        def interrupt() -> None:
            timed_out.set()
            try:
                handle.interrupt()
            except Exception as exc:  # the turn result remains the final authority
                log.warning("Codex interruption not confirmed: %s", exc)

        timer = threading.Timer(timeout, interrupt)
        timer.start()
        try:
            try:
                result = handle.run()
            except Exception as exc:
                if cancelled.is_set():
                    raise _CancelledError("Codex turn cancelled by the athlete") from exc
                if timed_out.is_set():
                    raise _WatchdogError(
                        "Codex turn interrupted after the maximum delay") from exc
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
            raise _CancelledError("Codex turn cancelled by the athlete")
        if timed_out.is_set():
            raise _WatchdogError("Codex turn interrupted after the maximum delay")
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
                    psid, thread, with_turn_input(message, turn_id), model=spec.model,
                    effort=spec.effort, timeout=spec.timeout,
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
                    error="Codex turn cancelled by /cancel", failure=FailureKind.CANCELLED,
                    work_started=True)
            except _WatchdogError:
                if session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self.provider)
                return RunResult(
                    error=f"Codex watchdog after {spec.timeout} s",
                    failure=FailureKind.WATCHDOG, work_started=True)
            except _InFlightError as exc:
                if session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self.provider)
                log.warning("Codex failed after work started: %s", exc)
                return RunResult(
                    error=f"Codex outage during the turn: {exc}"[:500],
                    failure=FailureKind.IN_FLIGHT, work_started=True)
            except ImportError as exc:
                log.error("Codex SDK missing: %s", exc)
                return RunResult(error="openai-codex SDK missing",
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
            error=f"Codex unavailable before any work: {last_error}"[:500],
            failure=FailureKind.PREFLIGHT)

    def one_shot(self, message: str, *, spec: RunSpec, output_schema: dict | None = None,
                 instructions: str | None = None, label: str = "__one_shot__") -> str:
        """An isolated turn, with no shell, session or constitution.

        Used for the triage of each message, on a reused process, and for the
        out-of-production evaluation bench. Raises the SDK exception on failure.
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
            # A dead process is not reused: the next call restarts it.
            self._close_one_shot_client()
            raise
        return result.final_response or ""

    def classify(self, message: str, *, spec: RunSpec | None = None) -> Triage:
        """A single fast classifier; its absence falls back to the deterministic default."""
        if not self._config.triage_enabled:
            return Triage()
        candidate = spec or self._config.routing.plan(Route.TRIAGE).primary
        try:
            raw = self.one_shot(TRIAGE_PROMPT + message, spec=candidate,
                                output_schema=TRIAGE_SCHEMA, label="__triage__")
            return parse_triage(json.loads(raw or "{}"))
        except (_WatchdogError, _CancelledError):
            log.warning("Codex triage timed out after %d s; falling back to N1", candidate.timeout)
        except Exception as exc:
            log.warning("Codex triage unavailable (%s); falling back to N1",
                        type(exc).__name__)
        return Triage()

