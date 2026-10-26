"""Contrat du moteur de conversation utilisé par le bot.

Ce module appartient au cœur de l'application. Les détails de transport restent
dans les implémentations Claude Code et Codex.
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
    """Cycle de vie demandé pour un tour de modèle."""

    CONTINUE = "continue"
    ISOLATED = "isolated"
    FORK = "fork"


class FailureKind(str, Enum):
    """Cause exploitable par l'orchestrateur, sans analyser un texte d'erreur."""

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
    """Décision du petit modèle avant le tour principal.

    `plan_change` et `plan_asked` répondent à deux questions différentes, et les avoir
    confondues coûtait cher : le premier dit qu'il faut RELIRE le plan avant de
    répondre, le second que Sam réclame son programme et qu'il doit donc partir.
    """

    depth: str = DEFAULT_DEPTH
    emotional: bool = False
    holding: str | None = None
    plan_change: bool = False
    plan_asked: bool = False
    plan_scope: str = "none"

    def __post_init__(self) -> None:
        # Compatibilité avec les appelants historiques qui ne portaient qu'un booléen.
        # Une demande explicite garde toujours le droit de faire répéter le programme ;
        # seule sa portée devient plus précise quand le triage la connaît.
        if self.plan_asked and self.plan_scope == "none":
            self.plan_scope = "remaining"
        if self.plan_scope not in PLAN_SCOPES:
            raise ValueError(f"portée de programme inconnue : {self.plan_scope!r}")
        if self.plan_scope != "none":
            self.plan_asked = True


@dataclass
class RunResult:
    """Résultat terminal, indépendant du fournisseur.

    ``error`` est un détail TECHNIQUE destiné au journal, jamais un texte pour Sam :
    seul ``bot.py`` décide de ce qui lui est dit, à partir de ``failure``.
    ``silent`` porte le choix typé d'une initiative de ne rien envoyer.
    """

    session_id: str | None = None
    full_text: str = ""
    error: str | None = None
    retry_at: float | None = None
    failure: FailureKind | None = None
    work_started: bool = False
    silent: bool = False

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
    """Paramètres d'un tour, indépendants du fournisseur.

    Le bot choisit une route dans ``routing.py`` et ne transmet jamais de modèle
    directement à un adaptateur. Cela permet à Claude et Codex de partager le même
    contrat, tout en gardant leurs noms de modèles dans une seule configuration.
    """

    model: str | None
    effort: str | None
    timeout: int
    session_mode: SessionMode = SessionMode.CONTINUE
    read_only: bool = False
    shell_enabled: bool = True
    # Réponse typée `{send, text}` : une initiative dit si elle parle sans qu'aucun
    # code n'ait à chercher un mot sentinelle dans sa prose.
    structured_reply: bool = False


#: Le contrat de sortie d'une initiative, partagé par les deux fournisseurs.
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
    """Valide la sortie typée d'une initiative ; lève ValueError si elle est illisible."""
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
    """Le travail en cours par PSID, pour les deux moteurs : ce que /status montre et
    ce que /cancel arrête. Chaque moteur ne dit que comment stopper son travail."""

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
        except Exception as exc:  # le résultat du tour reste l'autorité finale
            log.warning("Annulation %s non confirmée : %s", self._label, exc)
        return True

    def psids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._runs)


def live_session(state: StateStore, config: Config, provider: str,
                 psid: str) -> str | None:
    """La session à reprendre, ou None si elle a expiré ou suit d'anciennes instructions."""
    session_id = state.session_id(psid, provider)
    if not session_id:
        return None
    if state.session_age(psid, provider) > config.session_ttl:
        log.info("Session %s %s expirée (>%.0f h) : nouveau contexte depuis le vault",
                 provider, psid, config.session_ttl_hours)
    elif state.session_prompt(psid, provider) != instruction_digest(config.vault_path):
        log.info("Instructions du coach modifiées : session %s %s abandonnée", provider, psid)
    else:
        return session_id
    state.clear_session(psid, provider)
    return None


class AgentRunner(Protocol):
    """Port minimal dont dépend l'orchestrateur Messenger."""

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
