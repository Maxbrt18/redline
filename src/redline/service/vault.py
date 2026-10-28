"""Persistence boundary between PostgreSQL and the versioned vault views."""

from __future__ import annotations

import contextlib
import enum
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import exc as sa_exc

from redline.model import agent_input, session_cycle, vault_layout
from redline.model import ledger as ledger_model
from redline.model import paths as engine_paths
from redline.model import weekly_plan as weekly_plan_model
from redline.model.context_packet import render_context_packet
from redline.storage.database import StorageError
from redline.storage.repository import OperationalStore

from . import clock
from .plans import PlanStatus, render_plan_message

log = logging.getLogger("redline")

#: How the service regenerates the vault's views after delivery (`publish`).
REGEN_COMMAND = (sys.executable, "-m", "redline.cli.regen_views")
PROCESS_TIMEOUT = 120
GIT_TIMEOUT = 60
# A command already validated during the turn must survive a PostgreSQL restart:
# applying it is retried briefly before it is declared refused.
APPLY_RETRY_PAUSES = (1.0, 3.0, 8.0)
# A settled session no longer changes the review: done, missed, cancelled or closed.
SETTLED_CYCLE_STATES = session_cycle.TERMINAL_STATES | {"completed"}

# A live service may synchronize vault content, never executable/runtime files.
# Deploying those belongs to the service manager, outside a message transaction.
#
# "Runtime" means: the service behaves differently after this commit. A file that
# nothing executes or loads is not runtime, even under `deploy/`. For example
# `coaching/exercises/*.md` is DATA, reread by a subprocess on each `chart-strength.py`
# call, never loaded at startup, and edited by the coach itself. Classing it as runtime
# would freeze vault synchronization on a write the coach is allowed to make.
RUNTIME_PATHS = (
    "deploy/",
    "src/",
    "migrations/",
    ".github/",
    ".githooks/",
    "alembic.ini",
    "pyproject.toml",
)

#: The trees whose fingerprint identifies the deployed code (`/health`, release).
RUNTIME_TREES = ("deploy", "src", "migrations")

# Technical alert causes. The key avoids duplicating a cause within a single
# synchronization or persistence pass.
ALERT_DEPLOY = "deploiement"
ALERT_HISTORY = "historique"
ALERT_PUSH = "push"
ALERT_COMMIT = "commit"
ALERT_PROJECTION = "projection"
ALERT_WRITE = "ecriture"


def needs_deployment(path: str) -> bool:
    """Whether integrating this file would change how the running service behaves."""
    return path.startswith(RUNTIME_PATHS)

CANONICAL_PROJECTIONS = frozenset(str(path) for path in vault_layout.PROJECTIONS)


@dataclass(frozen=True)
class VaultAlert:
    """An internal incident and its cause, logged at most once before draining."""
    key: str
    text: str


class PersistenceFailure(enum.Enum):
    """Why the facts of a turn were not recorded.

    Forbidden files (hand-edited projection, modified code) no longer refuse the turn:
    they are restored and the rest proceeds. Only a failure to apply the commands
    themselves leaves facts unrecorded.
    """

    #: The facts could not be applied (schema, immutability, conflict, outage).
    WRITE_REFUSED = "écriture refusée"


@dataclass(frozen=True)
class PersistenceResult:
    writes_applied: bool
    detail: str = ""
    # Weeks recorded but not prescribable while a dated check holds.
    awaiting: tuple = field(default_factory=tuple)
    # Weeks recorded but stale: a session moved since they were computed.
    stale: tuple = field(default_factory=tuple)
    # Weeks absent or invalid: no session block may be sent.
    invalid: tuple = field(default_factory=tuple)
    # The typed cause, when the writes are refused.
    failure: PersistenceFailure | None = None
    applied_commands: int = 0
    receipts: tuple[dict, ...] = ()
    # Forbidden files the turn had touched, restored to their previous state.
    restored: tuple[str, ...] = ()
    # Kinds of the commands that were not recorded (write refused).
    refused: tuple[str, ...] = ()


def _transient(error: StorageError) -> bool:
    """A connection or lock failure is retried; a business refusal never is."""
    cause = error.__cause__
    return isinstance(cause, (sa_exc.OperationalError, sa_exc.InterfaceError)) or bool(
        getattr(cause, "connection_invalidated", False))


def forbidden_in_turn(path: str) -> bool:
    """What a coach turn may never modify in the vault."""
    return (path in CANONICAL_PROJECTIONS or path.startswith(f"{vault_layout.CAPABILITIES}/")
            or needs_deployment(path))


class VaultRepo:
    def __init__(self, path: str, remote: str, branch: str,
                 operational_store: OperationalStore | None = None,
                 regen_command: tuple[str, ...] = (),
                 engine_root: Path = engine_paths.ENGINE_ROOT) -> None:
        """`regen_command` regenerates the static views; without it, none are."""
        self._path = path
        self._engine_root = engine_root
        self._regen_command = regen_command
        self._remote = remote
        self._branch = branch
        self._alerts: list[VaultAlert] = []
        self._store = operational_store
        # A local commit awaits its push, views await regeneration (memory only: a
        # restart recovers the first through `_ahead_of_remote`, the second through
        # the PostgreSQL projection state).
        self._push_due = False
        self._views_due = False

    def _alert(self, key: str, text: str) -> None:
        """Queue an incident, once per cause until it is drained.

        `_push()` replays `sync()` before retrying: without this guard, a repository
        awaiting a deployment would raise the same message twice in one turn.
        """
        if any(alert.key == key for alert in self._alerts):
            return
        self._alerts.append(VaultAlert(key, text))

    def _pending(self, key: str) -> VaultAlert | None:
        return next((alert for alert in self._alerts if alert.key == key), None)

    def sync(self) -> bool:
        """Integrate remote vault-only commits and refuse runtime updates.

        Returning ``True`` means HEAD moved, so an existing model session must
        reread its context. A commit that touches runtime files stays in the remote
        branch until the normal deployment process restarts the service on it.
        """
        before = self._head()
        fetched = self._git("fetch", self._remote, self._branch)
        if fetched.returncode != 0:
            return False
        incoming = self._git("rev-parse", "FETCH_HEAD", log_errors=False).stdout.strip()
        if not before or not incoming or incoming == before:
            return False
        base = self._git(
            "merge-base", before, incoming, log_errors=False
        ).stdout.strip()
        if not base:
            self._alert(ALERT_HISTORY,
                        "Synchronisation Git refusée : historique distant incompatible")
            return False
        changed = self._git(
            "diff", "--name-only", base, incoming, log_errors=False
        ).stdout.splitlines()
        runtime = [path for path in changed if needs_deployment(path)]
        if runtime:
            detail = ", ".join(runtime[:5])
            log.warning("Déploiement requis avant synchronisation (%s)", detail)
            self._alert(ALERT_DEPLOY,
                        "Une mise à jour du runtime attend un déploiement : " + detail)
            return False
        if self._git("rebase", "--autostash", incoming).returncode != 0:
            self._git("rebase", "--abort", log_errors=False)
        after = self._head()
        return after != before

    def head(self) -> str:
        """Return the checked-out commit."""
        return self._head()

    def runtime_tree(self, ref: str = "HEAD") -> str:
        """Fingerprint of the engine CODE at ``ref``.

        It does not change when the coach commits, and it is read from the engine
        repository, whether the vault is a different one or the same.
        `deploy/verify-release.sh` recomputes the same value from the deployed SHA.
        """
        # The engine checkout belongs to the deploy account, not to the service user.
        trees = [self._git("-c", f"safe.directory={self._engine_root}", "rev-parse",
                           f"{ref}:{path}", log_errors=False,
                           cwd=self._engine_root).stdout.strip()
                 for path in RUNTIME_TREES]
        if not all(trees):
            return ""
        return hashlib.sha256(" ".join(trees).encode()).hexdigest()[:12]

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD", log_errors=False).stdout.strip()

    def is_clean(self) -> bool:
        return not self._git("status", "--porcelain").stdout.strip()

    def ensure_clean(self) -> list[str]:
        """Discard residue from a crashed turn and return the affected paths."""
        paths = self._changed_paths()
        if not paths:
            return []
        log.error("Worktree sale au début d'un tour — résidu d'un tour mort avant sa "
                  "persistance, abandonné : %s", ", ".join(paths[:10]))
        self._reset_worktree()
        return paths

    def abandon_turn(self, base_head: str = "") -> None:
        """Restore the worktree and HEAD to the state preceding a refused turn."""
        if base_head and self._head() not in ("", base_head):
            # The agent sometimes commits itself; a refused turn does not keep that
            # commit (never pushed: pushing only happens after a successful persistence).
            log.critical("Tour abandonné après un commit de l'agent : retour à %s",
                         base_head[:8])
            self._git("reset", "--hard", base_head)
        self._reset_worktree()

    def discard_turn(self, turn_id: str) -> None:
        if self._store is not None:
            self._store.discard_turn(turn_id)

    def clear_turn_inputs(self, turn_id: str) -> None:
        """Delete the JSON files handed to the CLIs by this turn (ignored by Git)."""
        try:
            agent_input.clear(turn_id, root=Path(self._path))
        except OSError as exc:
            log.warning("Fichiers du tour %s non supprimés : %s", turn_id, exc)

    def record_proactive_delivery(self, job, sent_at: datetime, message: str,
                                  turn_id: str = "") -> None:
        """Journalise un message seulement après sa livraison Messenger."""
        if self._store is None:
            raise StorageError("stockage PostgreSQL absent pour la trace proactive")
        kind = {
            "morning": "morning", "weekly": "weekly", "monthly": "monthly",
            "strava_debrief": "debrief", "strava_debrief_differe": "debrief",
        }.get(job.kind, "other")
        source_key = job.job_id or f"{job.kind}:{job.detail}:{sent_at.isoformat()}"
        self._store.add_proactive_message({
            "source_key": source_key,
            "sent_at": sent_at.astimezone(),
            "kind": kind,
            "angle": self._store.turn_angle(turn_id) or None,
            "summary": message[:2000],
        }, actor="messenger-delivery")

    def _reset_worktree(self) -> None:
        """`clean -fd` without `-x`: ignored files survive, including `var/`,
        whose images still await archiving."""
        self._git("reset", "--hard", "HEAD")
        self._git("clean", "-fd")

    def _changed_paths(self) -> list[str]:
        tracked = self._git("diff", "--name-only", "HEAD", log_errors=False).stdout.splitlines()
        untracked = self._git(
            "ls-files", "--others", "--exclude-standard", log_errors=False).stdout.splitlines()
        return list(dict.fromkeys([*tracked, *untracked]))

    def persist_turn(self, required_weeks=(), base_head: str = "",
                     turn_id: str = "") -> PersistenceResult:
        """Record a turn BEFORE its delivery, with no network call.

        1. A forbidden file (hand-edited PostgreSQL projection, code, capabilities) is
           restored: that effect is the dangerous one, not the rest of the turn.
        2. The prepared commands, already validated during the turn, are applied in
           PostgreSQL, with a short retry on a transient outage.
        3. The notes edited by the coach are committed locally, without a push.
        4. The required weeks and those the turn revised are reread.

        Projections, push and synchronization come after delivery (`publish`).
        ``required_weeks`` is empty for a social turn; a week whose dated check is due
        is recorded and returned in ``awaiting``.
        """
        if isinstance(required_weeks, str):
            required_weeks = (required_weeks,)
        base_head = base_head or self._head()
        restored = self._restore_forbidden(base_head)
        try:
            applied, receipts = self._apply(turn_id)
        except StorageError as exc:
            detail = str(exc)
            self._alert(ALERT_WRITE, detail)
            return PersistenceResult(False, detail=detail, restored=restored,
                                     failure=PersistenceFailure.WRITE_REFUSED,
                                     refused=self._refused_kinds(turn_id))
        # Weeks actually revised are checked even if triage did not identify a
        # program change.
        required_weeks = tuple(dict.fromkeys((*required_weeks, *(
            receipt["payload"]["week"] for receipt in receipts
            if receipt["kind"] == "plan.revise"))))
        if self._commit_local() or applied:
            self._views_due = True
        try:
            statuses = {week: self.validate_weekly_plan(week) for week in required_weeks}
        except Exception as exc:
            # The PostgreSQL transaction is already committed. A failed reread cannot
            # turn it into a refused write; it only withholds the sessions, since they
            # cannot be proven.
            log.exception("Relecture du programme impossible après le tour %s", turn_id)
            detail = f"faits enregistrés ; relecture interrompue : {type(exc).__name__}"
            self._alert(ALERT_PROJECTION, detail)
            return PersistenceResult(True, applied_commands=applied, receipts=receipts,
                                     detail=detail, invalid=required_weeks,
                                     restored=restored)
        invalid = tuple(week for week, status in statuses.items()
                        if status is PlanStatus.INVALID)
        awaiting = tuple(week for week, status in statuses.items()
                         if status is PlanStatus.AWAITING)
        stale = tuple(week for week, status in statuses.items()
                      if status is PlanStatus.STALE)
        if awaiting:
            log.info("Semaine(s) %s enregistrée(s) mais non prescriptible(s) : "
                     "un contrôle daté attend la réponse de ⟨athlète⟩", ", ".join(awaiting))
        if stale:
            log.info("Semaine(s) %s enregistrée(s) mais périmée(s) : une séance a bougé "
                     "depuis leur calcul, la prescription attend une révision",
                     ", ".join(stale))
        return PersistenceResult(True, applied_commands=applied, receipts=receipts,
                                 awaiting=awaiting, stale=stale, invalid=invalid,
                                 restored=restored)

    def _restore_forbidden(self, base_head: str) -> tuple[str, ...]:
        """Put the forbidden files back in their pre-turn state."""
        current = self._head()
        if base_head and current and current != base_head:
            # The agent committed itself: its changes become work in progress again,
            # judged like the rest, then committed by the service.
            log.warning("Commit de l'agent pendant le tour : repris dans le commit du service")
            self._git("reset", "--soft", base_head)
        forbidden = sorted(path for path in self._changed_paths() if forbidden_in_turn(path))
        if not forbidden:
            return ()
        known = set(self._git("ls-tree", "-r", "--name-only", "HEAD", "--", *forbidden,
                              log_errors=False).stdout.splitlines())
        tracked = [path for path in forbidden if path in known]
        created = [path for path in forbidden if path not in known]
        if tracked:
            self._git("checkout", "HEAD", "--", *tracked)
        if created:
            self._git("rm", "-q", "--cached", "--ignore-unmatch", "--", *created,
                      log_errors=False)
            for path in created:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(self._path, path))
        self._alert(ALERT_WRITE, "fichiers interdits restaurés pendant un tour coach : "
                    + ", ".join(forbidden))
        return tuple(forbidden)

    def _apply(self, turn_id: str) -> tuple[int, tuple[dict, ...]]:
        if not turn_id:
            return 0, ()
        if self._store is None:
            raise StorageError("stockage PostgreSQL absent à la frontière du tour")
        for pause in (0.0, *APPLY_RETRY_PAUSES):
            if pause:
                time.sleep(pause)
            try:
                return self._store.apply_turn_with_receipts(turn_id)
            except StorageError as exc:
                if not _transient(exc) or pause == APPLY_RETRY_PAUSES[-1]:
                    raise
                log.warning("Application du tour %s reportée (%s) ; nouvelle tentative",
                            turn_id, exc)
        raise AssertionError("boucle d'application épuisée")  # pragma: no cover

    def _refused_kinds(self, turn_id: str) -> tuple[str, ...]:
        if not turn_id or self._store is None:
            return ()
        try:
            return self._store.pending_turn_kinds(turn_id)
        except Exception:
            log.exception("Commandes refusées du tour %s illisibles", turn_id)
            return ()

    def _commit_local(self) -> bool:
        """Commit the coach notes with no hook or network; True if a commit is created."""
        paths = self._changed_paths()
        if not paths:
            return False
        staged = self._git("add", "-A", "--", *paths)
        if staged.returncode != 0:
            detail = (staged.stderr or staged.stdout).strip()[:200]
            self._alert(ALERT_COMMIT, f"Préparation du commit refusée par git : {detail}")
            return False
        stamp = clock.now().strftime("%Y-%m-%d %H:%M")
        # This boundary seals and regenerates itself; the hook is for human commits.
        committed = self._git("commit", "--no-verify", "-m", f"coach (messenger) - {stamp}")
        if committed.returncode != 0:
            detail = (committed.stderr or committed.stdout).strip()[:200]
            self._alert(ALERT_COMMIT, f"Commit du vault refusé par git : {detail}")
            return False
        self._push_due = True
        return True

    def publish(self) -> bool:
        """After delivery: views, commit and push, outside the response time.

        Git and the views are eventually consistent; PostgreSQL already has everything.
        Returns True when nothing is left to publish.
        """
        try:
            projection_due = (self._store.projection_pending()
                              if self._store is not None else False)
        except Exception as exc:
            self._alert(ALERT_PROJECTION, f"état des projections illisible : {exc}")
            projection_due = False
        if projection_due or self._views_due or self._changed_paths():
            self._views_due = False
            try:
                self.regenerate_views()
            except Exception as exc:
                self._alert(ALERT_PROJECTION,
                            "faits enregistrés dans PostgreSQL, projection à reprendre : "
                            f"{exc}")
        self._commit_local()
        if not self._push_due and not self._ahead_of_remote():
            return True
        pushed = self._push()
        if pushed:
            self._push_due = False
        return pushed

    def _ahead_of_remote(self) -> bool:
        counted = self._git("rev-list", "--count", f"{self._remote}/{self._branch}..HEAD",
                            log_errors=False)
        try:
            return counted.returncode == 0 and int(counted.stdout.strip() or 0) > 0
        except ValueError:
            return False

    def drain_alerts(self) -> list[VaultAlert]:
        """Return and clear internal persistence alerts, one per cause."""
        alerts, self._alerts = self._alerts, []
        return alerts

    def validate_weekly_plan(self, week: str, plan: dict | None = None) -> PlanStatus:
        """Classify a stored week for persistence and prescription."""
        if self._store is None:
            return PlanStatus.INVALID
        plan = plan if plan is not None else self._store.load_plan(week)
        if plan is None:
            return PlanStatus.INVALID
        try:
            weekly_plan_model.validate(plan)
        except Exception as exc:
            log.error("plan PostgreSQL %s invalide : %s", week, exc)
            return PlanStatus.INVALID
        if weekly_plan_model.blocking_gates(plan):
            return PlanStatus.AWAITING
        active = weekly_plan_model.active(plan)
        if self._store.plan_needs_arbitration(week, active["revision"]):
            log.info("plan PostgreSQL %s à réexaminer : une activité non rattachée est "
                     "apparue depuis son calcul", week)
            return PlanStatus.STALE
        return PlanStatus.VALID

    def activity_recorded(self, activity_id: str) -> bool | None:
        """Return true, false, or unknown for one exact Strava activity id."""
        if self._store is None:
            return None
        try:
            return self._store.activity_recorded(int(str(activity_id).strip()))
        except (ValueError, TypeError):
            log.error("Contrôle de consignation impossible : « %s » n'est pas un "
                      "identifiant d'activité", activity_id)
            return None
        except StorageError as exc:
            log.error("Contrôle de consignation impossible pour %s : %s", activity_id, exc)
            return None

    def pending_ledger_cells(self, today) -> list:
        """Declarative cells still inside their completion window."""
        return self.ledger_cells(today)[0]

    def ledger_cells(self, today) -> tuple[list, list]:
        """(still claimable, lapsed out of their window) from ONE ledger read."""
        if self._store is None:
            return [], []
        rows = self._store.observation_rows()
        return (ledger_model.pending_observation_fields(rows, today),
                ledger_model.lapsed_observation_fields(rows, today))

    def weekly_plan_context(self, week: str) -> str:
        """Return the active revision unless the week is absent or invalid."""
        if self._store is None:
            return ""
        plan = self._store.load_plan(week)
        if plan is None or self.validate_weekly_plan(week, plan) is PlanStatus.INVALID:
            return ""
        return weekly_plan_model.as_json(plan)

    def day_settled(self, day) -> bool:
        """All sessions planned that day are settled: done, missed or cancelled.

        A rest day is not awaited. Without a readable program, nothing is settled.
        """
        if self._store is None:
            return False
        week = weekly_plan_model.current_week(day)
        context = self.weekly_plan_context(week)
        if not context:
            return False
        keys = [session["id"] for session in json.loads(context)["sessions"]
                if session["date"] == day.isoformat() and session["kind"] != "rest"
                and session.get("status") != "cancelled"]
        links = self._store.week_session_links(week)
        return all((links.get(key) or {}).get("state") in SETTLED_CYCLE_STATES
                   for key in keys)

    def coaching_context(self, depth: str) -> str:
        """Bounded facts already available, before the agent decides to widen."""
        if self._store is None:
            return ""
        return render_context_packet(self._store, depth, clock.today())

    def weekly_dossier(self, week: str) -> str:
        """The computed review dossier of `week`; a failure is reported, never blocking.

        The review must still be possible if a computation fails: the model then reruns
        the CLIs itself.
        """
        if self._store is None:
            return ""
        from pathlib import Path

        from redline.analysis import weekly_dossier
        try:
            return weekly_dossier.render(self._store, Path(self._path), week, clock.today())
        except Exception:  # the dossier is an aid, never a gate
            log.exception("Dossier du bilan %s non calculé", week)
            return ("[DOSSIER DU BILAN indisponible : calcul en échec, voir les journaux. "
                    "Relance toi-même ledger.py, signals.py et fact-read.py predictions --due.]")

    def weekly_plan_message(self, week: str, today: str = "", since: str = "",
                            exclude_ids=frozenset()) -> str:
        """Render the persisted plan for today, the remaining week, or the full week."""
        return render_plan_message(self.weekly_plan_context(week), week,
                                   today=today, since=since, exclude_ids=exclude_ids)

    def render_weekly_plan_card(self, week: str, today: str = "") -> bool:
        """Render the same active revision into the Messenger outbox."""
        context = self.weekly_plan_context(week)
        if not context:
            return False
        try:
            revision = json.loads(context)["revision"]
        except (json.JSONDecodeError, KeyError):
            return False
        outbox = engine_paths.outbox_dir()
        outbox.mkdir(parents=True, exist_ok=True)
        target = str(outbox / f"plan-{week.lower()}-r{revision}.png")
        command = [sys.executable, "-m", "redline.cli.chart_plan", "--week", week, "-o", target]
        if today:
            command += ["--today", today]
        try:
            process = subprocess.run(
                command, cwd=self._path, capture_output=True, text=True,
                timeout=PROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.error("carte du plan %s non générée : timeout", week)
            return False
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip()[:500]
            log.error("carte du plan %s non générée : %s", week, detail)
            return False
        return True

    def render_workout_card(self, card: dict, activity_id: str) -> bool:
        """Render the repetitions card of a quality session into the outbox."""
        outbox = engine_paths.outbox_dir()
        outbox.mkdir(parents=True, exist_ok=True)
        target = str(outbox / f"workout-{activity_id}.png")
        try:
            process = subprocess.run(
                [sys.executable, "-m", "redline.cli.chart_workout", "-o", target],
                cwd=self._path,
                input=json.dumps(card, ensure_ascii=False), capture_output=True,
                text=True, timeout=PROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.error("carte de séance %s non générée : timeout", activity_id)
            return False
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip()[:500]
            log.error("carte de séance %s non générée : %s", activity_id, detail)
            return False
        return True

    def regenerate_views(self) -> None:
        """Rebuild every view; acknowledge projection state only after all succeed."""
        from pathlib import Path

        from redline.storage.projector import project_all
        if self._store is not None:
            project_all(self._store, Path(self._path), finalize=self._regenerate_static_views)
        else:
            self._regenerate_static_views()

    def _regenerate_static_views(self) -> None:
        if not self._regen_command:
            return
        process = subprocess.run(
            list(self._regen_command), cwd=self._path, capture_output=True,
            text=True, timeout=PROCESS_TIMEOUT)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip()[:300]
            log.error("régénération des vues échouée (%d) : %s",
                      process.returncode, detail)
            raise StorageError(
                "régénération des vues du vault échouée : " + detail)

    def _push(self) -> bool:
        """Push, and announce only ONE incident: the one that says what to do.

        A push refused while a deployment is pending is not a second incident but the
        CONSEQUENCE of the first: the remote is ahead by commits that `sync()` refused
        to integrate. Announcing them separately would give two messages, neither
        naming the action to take.
        """
        # If the remote moved in the meantime (a push from the Mac), rebase and retry.
        if self._git("push", self._remote, self._branch).returncode == 0:
            return True
        self.sync()
        if self._git("push", self._remote, self._branch).returncode == 0:
            return True
        blocked = self._pending(ALERT_DEPLOY)
        if blocked is not None:
            self._alerts.remove(blocked)
            self._alert(ALERT_DEPLOY, blocked.text
                        + " — d'ici là tes données sont enregistrées en local, "
                          "mais elles ne partent plus.")
        else:
            self._alert(ALERT_PUSH, "Commit enregistré localement mais push Git échoué")
        return False

    def _git(self, *args: str, log_errors: bool = True,
             cwd: str | Path | None = None) -> subprocess.CompletedProcess:
        command = ["git", *args]
        try:
            process = subprocess.run(
                command, cwd=cwd or self._path, capture_output=True, text=True,
                timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            process = subprocess.CompletedProcess(
                command, 124, "", f"timeout après {GIT_TIMEOUT} s")
        if log_errors and process.returncode != 0:
            detail = (process.stderr or process.stdout).strip()[:300]
            log.error("git %s failed (%d): %s", args[0], process.returncode, detail)
        return process
