"""Transactional boundary for one model turn."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace

from .agent import AgentRunner, RunResult, RunSpec, SessionMode
from .prompts import EMPTY_REPLY_NOTE
from .routing import RoutePlan
from .state import StateStore
from .vault import PersistenceFailure, PersistenceResult, VaultRepo

log = logging.getLogger("redline")


@dataclass(frozen=True)
class TurnOutcome:
    result: RunResult
    persistence: PersistenceResult | None
    base_head: str
    # The turn identity outlives its execution: a proactive trace is written AFTER the
    # Messenger delivery and needs to re-read what the turn left behind.
    turn_id: str = ""
    spec: RunSpec | None = None
    model_calls: int = 0


class TurnExecutor:
    """Run, persist or fully abandon a model turn.

    A turn produces exactly one reply: that of the model that did the work. No second
    writing pass re-reads or replaces it after persistence; the commands were already
    validated one by one during the turn (`stage_command`).
    """

    def __init__(self, agent: AgentRunner, repo: VaultRepo, state: StateStore) -> None:
        self._agent = agent
        self._repo = repo
        self._state = state

    def execute(self, psid: str, prompt: str, *, plan: RoutePlan,
                required_weeks=()) -> TurnOutcome:
        base_head = self._repo.head()
        turn_id = str(uuid.uuid4())
        self._state.register_turn(turn_id, psid)
        persisted: PersistenceResult | None = None
        result: RunResult | None = None
        used_spec = None
        retry_at: float | None = None
        calls = 0
        try:
            for attempt, spec in enumerate(plan.attempts):
                used_spec = spec
                result = self._agent.run(psid, prompt, turn_id=turn_id, spec=spec)
                calls += 1
                if not result.error:
                    break
                retry_at = retry_at or result.retry_at
                if attempt + 1 >= len(plan.attempts) or not result.allows_fallback:
                    if retry_at and not result.retry_at:
                        result = replace(result, retry_at=retry_at)
                    return TurnOutcome(result, None, base_head, turn_id, used_spec, calls)
                # A failed attempt may have left a file or a prepared command despite
                # its error. The transaction and the worktree are reset to their
                # initial state before trying the fallback profile.
                log.warning("Route %s en échec ; tentative de repli %d/%d",
                            plan.route, attempt + 2, len(plan.attempts))
                self._reset_for_retry(turn_id, base_head)
                if spec.session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self._agent.provider)
            if result is None or used_spec is None:  # pragma: no cover - route is never empty
                raise RuntimeError("route agent vide")
            if (not result.full_text.strip() and not result.silent
                    and not used_spec.structured_reply
                    and used_spec.session_mode is SessionMode.CONTINUE):
                continuation = self._agent.run(
                    psid, EMPTY_REPLY_NOTE, turn_id=turn_id, spec=used_spec)
                calls += 1
                if not continuation.error and continuation.full_text.strip():
                    result = continuation
                else:
                    log.warning("Tour %s sans texte final, même après relance", turn_id)
            try:
                persisted = self._repo.persist_turn(
                    required_weeks=required_weeks, base_head=base_head, turn_id=turn_id)
            except Exception as exc:
                log.exception("Échec inattendu à la frontière de persistance")
                persisted = PersistenceResult(
                    False,
                    detail=f"échec interne de persistance : {exc.__class__.__name__}",
                    failure=PersistenceFailure.WRITE_REFUSED,
                )
            return TurnOutcome(result, persisted, base_head, turn_id, used_spec, calls)
        finally:
            # The JSON files handed to the CLIs (`--input`, src/redline/model/agent_input.py)
            # do not outlive the turn: a later turn never re-reads them.
            self._repo.clear_turn_inputs(turn_id)
            if persisted is None or not persisted.writes_applied:
                self._repo.discard_turn(turn_id)
                self._repo.abandon_turn(base_head)
                if used_spec is None or used_spec.session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self._agent.provider)

    def read_only(self, psid: str, prompt: str, *, plan: RoutePlan,
                  turn_id: str | None = None) -> RunResult:
        """Run an analysis without persisting, with the same bounded fallback.

        Read-only is an application boundary: even without a Codex sandbox, any
        prepared PostgreSQL command and any Git change are destroyed before the reply
        is returned.
        """
        base_head = self._repo.head()
        actual_turn_id = turn_id or str(uuid.uuid4())
        last: RunResult | None = None
        try:
            for attempt, spec in enumerate(plan.attempts):
                last = self._agent.run(
                    psid, prompt, turn_id=actual_turn_id, spec=replace(spec, read_only=True))
                if not last.error or attempt + 1 >= len(plan.attempts):
                    return last
                if not last.allows_fallback:
                    return last
                log.warning("Analyse en lecture seule %s en échec ; repli %d/%d",
                            plan.route, attempt + 2, len(plan.attempts))
                self._reset_for_retry(actual_turn_id, base_head)
            return last or RunResult(error="Aucun profil de lecture disponible")
        finally:
            self._repo.discard_turn(actual_turn_id)
            self._repo.clear_turn_inputs(actual_turn_id)
            try:
                dirty = self._repo.head() != base_head or not self._repo.is_clean()
            except Exception:
                dirty = True
            if dirty:
                self._repo.abandon_turn(base_head)

    def _reset_for_retry(self, turn_id: str, base_head: str) -> None:
        """Clean up a failed attempt without needlessly resetting Git."""
        self._repo.discard_turn(turn_id)
        try:
            dirty = self._repo.head() != base_head or not self._repo.is_clean()
        except Exception:
            dirty = True
        if dirty:
            self._repo.abandon_turn(base_head)
