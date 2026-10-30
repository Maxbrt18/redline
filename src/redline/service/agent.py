"""Contract of the conversation engine used by the bot.

This module belongs to the application core. Transport details stay in the
Claude Code and Codex implementations.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from .prompts import DEFAULT_DEPTH, instruction_digest

if TYPE_CHECKING:
    from .config import Config
    from .state import StateStore

log = logging.getLogger("redline")

HOLDING_MAX_CHARS = 220
PLAN_SCOPES = frozenset({"none", "today", "next", "remaining", "full"})


class SessionMode(str, Enum):
    """Lifecycle requested for a model turn."""

    CONTINUE = "continue"
    ISOLATED = "isolated"
    FORK = "fork"


class FailureKind(str, Enum):
    """Cause the orchestrator can act on, without parsing an error text."""

    PREFLIGHT = "preflight"
    RATE_LIMIT = "rate_limit"
    IN_FLIGHT = "in_flight"
    WATCHDOG = "watchdog"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentActivity:
    model: str | None
    effort: str | None
    started_at: float
    watchdog_seconds: int
    cancelling: bool = False

    @property
    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)


@dataclass
class Triage:
    """Decision of the small model before the main turn.

    `plan_change` and `plan_asked` answer two different questions and must not be
    confused: the first says the plan must be RE-READ before answering, the second
    that the athlete is asking for their program, which must therefore be sent.
    """

    depth: str = DEFAULT_DEPTH
    emotional: bool = False
    holding: str | None = None
    plan_change: bool = False
    plan_asked: bool = False
    plan_scope: str = "none"

    def __post_init__(self) -> None:
        # Compatibility with callers that only carried a boolean. An explicit request
        # always keeps the right to have the program repeated; only its scope becomes
        # more precise when triage knows it.
        if self.plan_asked and self.plan_scope == "none":
            self.plan_scope = "remaining"
        if self.plan_scope not in PLAN_SCOPES:
            raise ValueError(f"unknown plan scope: {self.plan_scope!r}")
        if self.plan_scope != "none":
            self.plan_asked = True


#: What a triage call answers, for both providers.
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


def parse_triage(data: dict) -> Triage:
    """A triage answer checked against `TRIAGE_SCHEMA`; raises on any deviation, so the
    caller falls back to `Triage()`."""
    depth = data["depth"]
    holding = data["holding"]
    if depth not in {"N0", "N1", "N2", "N3"}:
        raise ValueError(f"invalid triage depth: {depth!r}")
    if holding is not None and not isinstance(holding, str):
        raise TypeError("holding must be a string or null")
    if not all(isinstance(data[flag], bool)
               for flag in ("emotional", "plan_change", "plan_asked")):
        raise TypeError("the triage flags must be booleans")
    plan_scope = data["plan_scope"]
    if plan_scope not in PLAN_SCOPES:
        raise ValueError(f"invalid plan scope: {plan_scope!r}")
    if data["plan_asked"] != (plan_scope != "none"):
        raise ValueError("plan_asked and plan_scope contradict each other")
    # The small model answering the question instead of announcing the wait.
    if holding and len(holding) > HOLDING_MAX_CHARS:
        holding = None
    return Triage(depth=depth, emotional=data["emotional"], holding=holding or None,
                  plan_change=data["plan_change"], plan_asked=data["plan_asked"],
                  plan_scope=plan_scope)


@dataclass
class RunResult:
    """Terminal result, independent of the provider.

    ``error`` is a TECHNICAL detail meant for the log, never a text for the athlete:
    only ``worker.py`` decides what is said to them, based on ``failure``.
    ``silent`` carries the typed choice of an initiative to send nothing.
    """

    session_id: str | None = None
    full_text: str = ""
    error: str | None = None
    retry_at: float | None = None
    failure: FailureKind | None = None
    work_started: bool = False
    silent: bool = False
    # The model the provider says answered; None when it does not say.
    model: str | None = None

    @property
    def allows_fallback(self) -> bool:
        return bool(
            self.error and not self.work_started
            and self.failure in {FailureKind.PREFLIGHT, FailureKind.RATE_LIMIT}
        )

    @property
    def terminal_failure(self) -> bool:
        return bool(self.error and not self.allows_fallback)


@dataclass(frozen=True)
class RunSpec:
    """Parameters of a turn, independent of the provider.

    The bot picks a route in ``routing.py`` and never passes a model directly to an
    adapter. This lets Claude and Codex share the same contract while keeping their
    model names in a single configuration.
    """

    model: str | None
    effort: str | None
    timeout: int
    session_mode: SessionMode = SessionMode.CONTINUE
    read_only: bool = False
    shell_enabled: bool = True
    # Typed `{send, text}` reply: an initiative says whether it speaks without any
    # code having to look for a sentinel word in its prose.
    structured_reply: bool = False


@dataclass(frozen=True)
class TurnIdentity:
    """What produced a model turn: the provider, the model and the versions it ran under."""

    turn_id: str
    psid: str
    provider: str
    route: str
    model: str | None
    model_reported: bool
    effort: str | None
    instruction_digest: str
    rulebook_digest: str
    engine_tree: str | None


#: Output contract of an initiative, shared by both providers.
PROACTIVE_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "send": {"type": "boolean"},
        "text": {"type": "string"},
    },
    "required": ["send", "text"],
    "additionalProperties": False,
}


def structured_reply(raw: object) -> tuple[bool, str]:
    """Validate the typed output of an initiative; raise ValueError if it is unreadable."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    if (not isinstance(data, dict) or set(data) != {"send", "text"}
            or type(data["send"]) is not bool or not isinstance(data["text"], str)):
        raise ValueError("sortie d'initiative hors contrat {send, text}")
    text = data["text"].strip()
    return (bool(data["send"] and text), text if data["send"] else "")


@dataclass
class ActiveRun:
    activity: AgentActivity
    stop: Callable[[], object]
    cancelled: threading.Event = field(default_factory=threading.Event)


class ActiveRuns:
    """The work in progress per PSID, for both engines: what /status shows and what
    /cancel stops. Each engine only says how to stop its own work."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._lock = threading.Lock()
        self._runs: dict[str, ActiveRun] = {}

    def start(self, psid: str, activity: AgentActivity,
              stop: Callable[[], object]) -> ActiveRun:
        run = ActiveRun(activity, stop)
        with self._lock:
            self._runs[psid] = run
        return run

    def finish(self, psid: str, run: ActiveRun) -> None:
        with self._lock:
            if self._runs.get(psid) is run:
                del self._runs[psid]

    def activity(self, psid: str) -> AgentActivity | None:
        with self._lock:
            run = self._runs.get(psid)
            return replace(run.activity) if run else None

    def cancel(self, psid: str) -> bool:
        with self._lock:
            run = self._runs.get(psid)
            if run is None:
                return False
            run.cancelled.set()
            run.activity = replace(run.activity, cancelling=True)
        try:
            run.stop()
        except Exception as exc:  # the turn result remains the final authority
            log.warning("Cancellation of %s not confirmed: %s", self._label, exc)
        return True

    def psids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._runs)


def live_session(state: StateStore, config: Config, provider: str,
                 psid: str) -> str | None:
    """The session to resume, or None if it expired or follows outdated instructions."""
    session_id = state.session_id(psid, provider)
    if not session_id:
        return None
    if state.session_age(psid, provider) > config.session_ttl:
        log.info("Session %s %s expired (>%.0f h): fresh context from the vault",
                 provider, psid, config.session_ttl_hours)
    elif state.session_prompt(psid, provider) != instruction_digest(config.vault_path):
        log.info("Coach instructions changed: session %s %s dropped", provider, psid)
    else:
        return session_id
    state.clear_session(psid, provider)
    return None


class AgentRunner(Protocol):
    """Minimal port the Messenger orchestrator depends on."""

    provider: str

    def run(
        self,
        psid: str,
        message: str,
        *,
        spec: RunSpec,
        turn_id: str | None = None,
    ) -> RunResult:
        ...

    def classify(self, message: str, *, spec: RunSpec) -> Triage:
        ...

    def activity(self, psid: str) -> AgentActivity | None:
        ...

    def cancel(self, psid: str) -> bool:
        ...

    def close(self) -> None:
        ...
