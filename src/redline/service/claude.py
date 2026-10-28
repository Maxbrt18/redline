"""Run Claude headlessly and return one terminal, buffered result."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import clock, persona
from .agent import (
    HOLDING_MAX_CHARS,
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
from .prompts import DEFAULT_DEPTH, STYLE_PROMPT, TRIAGE_PROMPT, instruction_digest, with_turn_input
from .routing import Route
from .state import StateStore

log = logging.getLogger("redline")

_DEPTH = re.compile(r"\bN[0-3]\b", re.IGNORECASE)
_RESET_EPOCH = re.compile(r"\|(\d{9,})")
_RESET_CLOCK = re.compile(r"reset[s]?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def _parse_reset_epoch(detail: str, now: datetime | None = None) -> int | None:
    """Reset instant extracted from a Claude limit message.

    Two known CLI formats: an epoch after a "|" (...|1752320400) or a wall-clock
    time ("resets 1:30pm"). For wall-clock we aim at the next occurrence in the
    VPS local time (assumed Paris time, like the CLI messages). Returns None if
    nothing is readable.
    """
    match = _RESET_EPOCH.search(detail)
    if match:
        return int(match.group(1))
    match = _RESET_CLOCK.search(detail)
    if not match:
        return None
    hour = int(match.group(1)) % 24
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    now = now or clock.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=clock.zone())
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int(target.timestamp())


#: Ce qu'un tour autonome peut faire. Partagé avec `deploy/claude-runtime-smoke.py`,
#: qui refuse une nouvelle version de Claude Code si elle bloque la forme exacte d'un
#: appel de CLI du coach : la 2.1.287 a refusé `… < /tmp/x.json` le 11/10.
AUTONOMOUS_PERMISSIONS = (
    "--permission-mode", "dontAsk",
    "--tools", "Read,Glob,Grep,Edit,Write,Bash",
    "--allowedTools",
    "Read Glob Grep Edit Write Bash(/usr/local/bin/redline-agent-cli *)",
)


@dataclass
class _Stream:
    """Terminal content extracted from the stream-json wire format."""

    full: str = ""
    session: str | None = None
    result_text: str | None = None
    is_error: bool = False
    # Le modèle a produit au moins un message : un échec ensuite n'est plus un
    # échec de démarrage, et le tour ne se rejoue pas.
    started: bool = False
    structured: object = None


class ClaudeCodeRunner:
    """Runs `claude -p` with session continuity and terminal result semantics."""

    provider = "claude"
    # Tentatives du même profil tant qu'aucun travail n'a commencé (session morte,
    # binaire qui tombe au démarrage) ; au-delà, la route essaie son profil de repli.
    MAX_ATTEMPTS = 3
    RETRY_DELAY = 5  # seconds, multiplied by the attempt number

    def __init__(self, config: Config, state: StateStore) -> None:
        self._config = config
        self._state = state
        self._runs = ActiveRuns("Claude")

    def activity(self, psid: str) -> AgentActivity | None:
        return self._runs.activity(psid)

    def cancel(self, psid: str) -> bool:
        return self._runs.cancel(psid)

    def _agent_env(self, turn_id: str | None = None) -> dict:
        """L'environnement du CLI, purgé de ce qu'il n'a pas à connaître.

        `CLAUDE_CODE_OAUTH_TOKEN` reste : c'est avec lui que le binaire s'authentifie.
        Tout le reste des secrets du service (jeton de page, app secret, etc.) est
        vidé — le CLI ne poste aucun message, et un secret
        présent dans un environnement finit tôt ou tard écrit sur le disque par un
        outil qui journalise son contexte.
        """
        environment = {
            **os.environ,
            **sandbox_overrides(keep=("CLAUDE_CODE_OAUTH_TOKEN",)),
        }
        if turn_id:
            environment["REDLINE_TURN_ID"] = turn_id
        else:
            environment.pop("REDLINE_TURN_ID", None)
        return environment

    def run(self, psid: str, message: str, *, spec: RunSpec | None = None,
            turn_id: str | None = None) -> RunResult:
        """Query Claude, retrying as long as no work has started.

        An isolated turn starts fresh. A fork resumes the interactive context into
        a throwaway session. Neither mode persists its result.
        """
        spec = spec or self._config.routing.plan(Route.N1).primary
        isolated = spec.session_mode is SessionMode.ISOLATED
        fork_session = spec.session_mode is SessionMode.FORK
        last_detail = ""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            source_session = None if isolated else live_session(self._state, self._config, self.provider, psid)
            can_fork = bool(fork_session and source_session)
            session_id = source_session if not fork_session or can_fork else None
            try:
                proc = subprocess.Popen(
                    self._command(message, session_id, spec.model, spec.effort,
                                  turn_id=turn_id,
                                  fork_session=can_fork, read_only=spec.read_only,
                                  shell_enabled=spec.shell_enabled,
                                  structured=spec.structured_reply),
                    cwd=self._config.vault_path,
                    env=self._agent_env(turn_id),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                )
            except OSError as exc:
                log.error("claude binary not found: %s", exc)
                return RunResult(error=f"binaire Claude introuvable : {exc}",
                                 failure=FailureKind.PREFLIGHT)

            active = self._runs.start(
                psid, AgentActivity(spec.model, spec.effort, time.monotonic(), spec.timeout),
                proc.terminate)
            cancelled = active.cancelled

            # stderr drained continuously: if only read at the end, the OS pipe
            # (~64 KB) would fill up and block the CLI mid-generation.
            stderr_lines: list = []
            drainer = threading.Thread(target=stderr_lines.extend, args=(proc.stderr,),
                                       daemon=True, name="stderr-drain")
            drainer.start()

            killed = threading.Event()
            # Bound as defaults, not captured: a timer that fires just as `cancel()`
            # runs would otherwise kill the process of the NEXT retry, since both
            # names are rebound at the top of each attempt.
            timer = threading.Timer(
                spec.timeout,
                lambda flag=killed, process=proc: (flag.set(), process.kill()))
            timer.start()
            if proc.stdout is None:   # we asked for PIPE; this cannot happen
                raise RuntimeError("claude : stdout non capturé malgré subprocess.PIPE")
            try:
                parsed = self._consume(proc.stdout)
                returncode = proc.wait()
            finally:
                timer.cancel()
                self._runs.finish(psid, active)
            drainer.join(timeout=5)
            stderr = "".join(stderr_lines).strip()

            if parsed.session and not isolated and not fork_session:
                self._state.set_session(psid, self.provider, parsed.session,
                                        instruction_digest(self._config.vault_path))

            if cancelled.is_set():
                return RunResult(parsed.session, error="tour Claude annulé par /cancel",
                                 failure=FailureKind.CANCELLED, work_started=True)
            if killed.is_set():
                return RunResult(parsed.session,
                                 error=f"watchdog Claude après {spec.timeout} s",
                                 failure=FailureKind.WATCHDOG, work_started=True)

            if returncode == 0 and not parsed.is_error:
                if not spec.structured_reply:
                    return RunResult(parsed.session, parsed.full)
                try:
                    send, text = structured_reply(parsed.structured)
                except (TypeError, ValueError) as exc:
                    return RunResult(parsed.session,
                                     error=f"sortie Claude hors contrat : {exc}",
                                     failure=FailureKind.IN_FLIGHT, work_started=True)
                return RunResult(parsed.session, text, silent=not send)

            last_detail = (parsed.result_text or stderr or "").strip()[:500]
            log.warning("claude failed (attempt %d/%d, code %d, session %s): %s",
                        attempt, self.MAX_ATTEMPTS, returncode, session_id or "none",
                        last_detail or "(no detail)")
            failure, retry_at = self._classify_failure(last_detail)
            if failure is not None:
                return RunResult(error=f"Claude indisponible : {last_detail}"[:500],
                                 retry_at=retry_at, failure=failure, work_started=False)
            if parsed.started:
                if session_id and not isolated and not fork_session:
                    self._state.clear_session(psid, self.provider)
                return RunResult(error=f"panne Claude en cours de tour : {last_detail}"[:500],
                                 failure=FailureKind.IN_FLIGHT, work_started=True)
            if session_id:
                # Rien n'a démarré avec cette session : elle est probablement morte
                # (« No conversation found »). La tentative suivante repart sans elle.
                self._state.clear_session(psid, self.provider)
            if attempt < self.MAX_ATTEMPTS:
                time.sleep(self.RETRY_DELAY * attempt)

        return RunResult(error=f"Claude indisponible avant tout travail : {last_detail}"[:500],
                         failure=FailureKind.PREFLIGHT)

    def classify(self, message: str, *, spec: RunSpec | None = None) -> Triage:
        """Triage an incoming message -- see CLAUDE.md and TRIAGE_PROMPT.

        A one-shot call on a small fast model. It only decides how much the real run
        will read (plus a tone hint and a holding line), so it must never block a
        reply: ANY failure falls back to `Triage()`, the behaviour the bot had before
        triage existed.
        """
        if not self._config.triage_enabled:
            return Triage()
        route = self._config.routing.plan(Route.TRIAGE)
        candidates = (spec or route.primary, *route.fallbacks)
        for attempt, candidate in enumerate(candidates, start=1):
            command = [self._config.claude_bin, "-p", self._personal(TRIAGE_PROMPT + message)]
            if candidate.model:
                command += ["--model", candidate.model]
            if candidate.read_only:
                command += ["--permission-mode", "dontAsk", "--tools", "Read,Glob,Grep"]
            # Le triage est lui aussi une requête Claude : sans ce flag, chaque message
            # payait un premier appel au défaut xhigh avant même la réponse réelle.
            if candidate.effort:
                command += ["--effort", candidate.effort]
            try:
                proc = subprocess.run(command, cwd=self._config.vault_path,
                                      env=self._agent_env(), capture_output=True,
                                      text=True, timeout=candidate.timeout)
            except subprocess.TimeoutExpired as exc:
                log.warning("Triage Claude expiré (%d/%d, %s)",
                            attempt, len(candidates), exc)
                continue
            except OSError as exc:
                log.warning("Triage Claude échoué (%d/%d, %s)",
                            attempt, len(candidates), exc)
                continue
            if proc.returncode != 0:
                log.warning("Triage Claude échoué (%d/%d, code %d)",
                            attempt, len(candidates), proc.returncode)
                continue
            parsed = self._parse_triage(proc.stdout or "")
            if parsed is not None:
                return parsed
            log.warning("Triage Claude illisible (%d/%d)", attempt, len(candidates))
        log.warning("Triage Claude indisponible après %d profils ; repli sur N1",
                    len(candidates))
        return Triage()

    def one_shot(self, message: str, *, spec: RunSpec, output_schema: dict | None = None,
                 instructions: str | None = None, label: str = "__one_shot__") -> str:
        """Un tour isolé, sans outil, sans session ni constitution : le contrat de
        `CodexRunner.one_shot`, pour le banc d'évaluation hors production.

        Le dossier de travail est vide : `CLAUDE.md` du vault ne se charge pas, comme
        Codex n'en chargeait aucun. `instructions` remplace le prompt système. Avec
        `output_schema`, rend la sortie structurée sérialisée en JSON. Lève en cas
        d'échec : le banc doit voir l'erreur, pas une réponse vide.
        """
        command = [self._config.claude_bin, "-p", self._personal(message),
                   "--output-format", "json", "--permission-mode", "dontAsk", "--tools", ""]
        if instructions:
            command += ["--system-prompt", self._personal(instructions)]
        if output_schema is not None:
            command += ["--json-schema", json.dumps(output_schema)]
        if spec.model:
            command += ["--model", spec.model]
        if spec.effort:
            command += ["--effort", spec.effort]
        with tempfile.TemporaryDirectory(prefix="redline-one-shot-") as cwd:
            proc = subprocess.run(command, cwd=cwd, env=self._agent_env(),
                                  capture_output=True, text=True, timeout=spec.timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"{label} : claude a rendu le code {proc.returncode}")
        payload = json.loads(proc.stdout or "{}")
        if payload.get("is_error"):
            raise RuntimeError(f"{label} : {str(payload.get('result', ''))[:200]}")
        if output_schema is not None:
            structured = payload.get("structured_output")
            if structured is None:
                raise RuntimeError(f"{label} : aucune sortie structurée")
            return json.dumps(structured, ensure_ascii=False)
        return str(payload.get("result") or "")

    def close(self) -> None:
        """Claude Code ne conserve aucun processus entre deux tours."""
        for psid in self._runs.psids():
            self.cancel(psid)

    @staticmethod
    def _parse_triage(raw: str) -> Triage | None:
        """Parse sans masquer l'absence de résultat, pour activer le fallback."""
        line = next((part.strip() for part in (raw or "").splitlines() if part.strip()), "")
        fields = [field.strip() for field in line.split("|")]
        match = _DEPTH.search(fields[0] if fields else "")
        if not match:
            log.warning("Triage returned no level (%r): falling back to %s",
                        line[:80], DEFAULT_DEPTH)
            return None
        depth = match.group(0).upper()
        emotional = len(fields) > 1 and fields[1].upper() == "E"
        holding = fields[2].strip() if len(fields) > 2 else ""
        plan_change = len(fields) > 3 and fields[3].upper() == "P"
        plan_asked = len(fields) > 4 and fields[4].upper() == "D"
        plan_scope = fields[5].lower() if len(fields) > 5 else (
            "remaining" if plan_asked else "none")
        if plan_scope not in {"none", "today", "next", "remaining", "full"}:
            plan_scope = "remaining" if plan_asked else "none"
        if plan_asked != (plan_scope != "none"):
            plan_scope = "remaining" if plan_asked else "none"
        if len(holding) > HOLDING_MAX_CHARS:
            log.info("Triage holding line dropped (%d chars): the model answered instead "
                     "of announcing the wait", len(holding))
            holding = ""
        return Triage(
            depth, emotional, holding or None, plan_change, plan_asked, plan_scope)

    @staticmethod
    def _consume(lines: Iterable[str]) -> _Stream:
        """Buffer the wire stream and retain only text after the last tool action."""
        state = _Stream()

        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("session_id"):
                state.session = obj["session_id"]
            kind = obj.get("type")
            if kind == "assistant":
                state.started = True
                separate = True  # first text block of this message: separate it from the accumulated
                for block in obj.get("message", {}).get("content", []):
                    btype = block.get("type")
                    if btype == "tool_use":
                        # Text written BEFORE an action is work narration ("I'm
                        # updating the journal..."), not a message: only the text
                        # after the LAST action goes to the athlete. We drop only what
                        # precedes it because nothing is delivered before completion.
                        state.full = ""
                        separate = True
                    elif btype == "text":
                        text = block.get("text", "")
                        if separate:
                            # Two consecutive assistant messages would stick
                            # together without a separator ("I'm writing.I just saw").
                            if state.full.strip():
                                state.full += "\n\n"
                            separate = False
                        state.full += text
            elif kind == "result":
                state.result_text = obj.get("result")
                state.is_error = bool(obj.get("is_error"))
                state.structured = obj.get("structured_output")
        if not state.full:
            state.full = state.result_text or ""
        return state

    @staticmethod
    def _classify_failure(detail: str) -> tuple[FailureKind | None, float | None]:
        """Classe une panne connue du CLI : (cause, instant de reprise lisible).

        Aucun texte pour l'athlète ne sort d'ici : le bot décide seul de ce qu'il dit.
        Une panne inconnue rend (None, None) et suit la règle « travail commencé ou
        non ».
        """
        lowered = detail.lower()
        if any(w in lowered for w in ("usage limit", "rate limit", "session limit")):
            reset_ts = _parse_reset_epoch(detail)
            return FailureKind.RATE_LIMIT, (float(reset_ts) if reset_ts else None)
        if any(word in lowered for word in ("oauth", "authentication", "api key",
                                             "please run /login")):
            log.error("Accès Claude expiré : sur le VPS, `claude setup-token` puis mettre "
                      "à jour CLAUDE_CODE_OAUTH_TOKEN dans l'environnement du service")
            return FailureKind.PREFLIGHT, None
        return None, None

    def _personal(self, text: str) -> str:
        """Le prénom de l'instance à la place du jeton : tout ce que le modèle lit."""
        return persona.render(text, self._config.instance.athlete_name)

    def _command(self, message: str, session_id: str | None,
                 model: str | None = None, effort: str | None = None,
                 *, turn_id: str | None = None, fork_session: bool = False,
                 read_only: bool = False, shell_enabled: bool = True,
                 structured: bool = False) -> list:
        command = [self._config.claude_bin, "-p",
                   self._personal(with_turn_input(message, turn_id)),
                   "--output-format", "stream-json", "--verbose",
                   "--append-system-prompt", self._personal(STYLE_PROMPT)]
        if structured:
            command += ["--json-schema", json.dumps(PROACTIVE_REPLY_SCHEMA)]
        if not shell_enabled:
            command += ["--permission-mode", "dontAsk", "--tools", ""]
        elif read_only:
            # La pièce jointe est une source non fiable : le moteur peut la lire, mais
            # ne dispose matériellement d'aucun outil d'écriture ni d'exécution.
            command += ["--permission-mode", "dontAsk", "--tools", "Read,Glob,Grep"]
        else:
            # Le canal autonome a besoin d'éditer le vault et d'appeler ses CLI, pas
            # d'un shell général. `dontAsk` refuse toute capacité hors de l'allowlist
            # au lieu de bloquer un processus sans terminal en attendant une réponse.
            command += list(AUTONOMOUS_PERMISSIONS)
        if model is None:
            model = self._config.routing.plan(Route.N1).primary.model
        if model:
            command += ["--model", model]
        if effort:
            command += ["--effort", effort]
        if session_id:
            command += ["--resume", session_id]
            if fork_session:
                command += ["--fork-session"]
        return command
