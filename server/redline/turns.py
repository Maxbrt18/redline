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
    # L'identité du tour survit à son exécution : la trace d'un proactif s'écrit APRÈS
    # la livraison Messenger, et elle a besoin de relire ce que le tour a déposé.
    turn_id: str = ""
    spec: RunSpec | None = None
    model_calls: int = 0


class TurnExecutor:
    """Run, persist or fully abandon a model turn.

    Un tour produit exactement une réponse : celle du modèle qui a travaillé. Aucune
    seconde rédaction ne la relit ni ne la remplace après persistance ; les commandes
    ont déjà été validées une à une pendant le tour (`stage_command`).
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
                # Une tentative ratée peut avoir laissé un fichier ou une commande
                # préparée malgré son erreur. On remet la transaction et le worktree
                # dans l'état initial avant d'essayer le profil de repli.
                log.warning("Route %s en échec ; tentative de repli %d/%d",
                            plan.route, attempt + 2, len(plan.attempts))
                self._reset_for_retry(turn_id, base_head)
                if spec.session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self._agent.provider)
            if result is None or used_spec is None:  # pragma: no cover - route non vide
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
            # Les JSON remis aux CLI (`--input`, scripts/model/agent_input.py) ne
            # survivent pas au tour : un tour suivant ne relit jamais ceux-ci.
            self._repo.clear_turn_inputs(turn_id)
            if persisted is None or not persisted.writes_applied:
                self._repo.discard_turn(turn_id)
                self._repo.abandon_turn(base_head)
                if used_spec is None or used_spec.session_mode is SessionMode.CONTINUE:
                    self._state.clear_session(psid, self._agent.provider)

    def read_only(self, psid: str, prompt: str, *, plan: RoutePlan,
                  turn_id: str | None = None) -> RunResult:
        """Exécute une analyse sans persister, avec le même fallback borné.

        La lecture seule est une frontière applicative : même sans sandbox Codex,
        toute commande PostgreSQL préparée et tout changement Git sont détruits avant
        de rendre la réponse.
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
        """Nettoie une tentative échouée sans réinitialiser inutilement Git."""
        self._repo.discard_turn(turn_id)
        try:
            dirty = self._repo.head() != base_head or not self._repo.is_clean()
        except Exception:
            dirty = True
        if dirty:
            self._repo.abandon_turn(base_head)
