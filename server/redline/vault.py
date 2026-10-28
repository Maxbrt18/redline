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

from scripts.model import agent_input, session_cycle, vault_layout
from scripts.model import ledger as ledger_model
from scripts.model import weekly_plan as weekly_plan_model
from scripts.model.context_packet import render_context_packet
from scripts.storage.database import StorageError
from scripts.storage.repository import OperationalStore

from . import clock
from .plans import PlanStatus, render_plan_message

log = logging.getLogger("redline")

# Views are regenerated after delivery (`publish`); hooks only cover configured clones.
REGEN = os.path.join("scripts", "regen-views.py")
PROCESS_TIMEOUT = 120
GIT_TIMEOUT = 60
# Une commande déjà validée pendant le tour ne se perd pas sur un redémarrage de
# PostgreSQL : l'application est retentée brièvement avant d'être déclarée refusée.
APPLY_RETRY_PAUSES = (1.0, 3.0, 8.0)
# Une séance soldée ne changera plus le bilan : faite, manquée, annulée ou close.
SETTLED_CYCLE_STATES = session_cycle.TERMINAL_STATES | {"completed"}

# A live service may synchronize vault content, never executable/runtime files.
# Deploying those belongs to the service manager, outside a message transaction.
#
# ⭐ « Runtime » veut dire : *le service tourne différemment après ce commit*. Un
# fichier que rien n'exécute ni ne charge n'en est pas, même s'il vit sous `server/`.
# La liste avait dérivé vers « tout ce qui ressemble à du code » et deux familles y
# étaient entrées à tort — d'où le blocage du 2026-08-21, causé par quatre fiches
# d'exercice et un fichier de test, dont aucun ne demande le moindre redémarrage :
#   - `coaching/exercises/*.md` : des DONNÉES, relues à chaque appel de
#     `chart-strength.py` dans un sous-processus. Rien ne les charge au démarrage, et
#     c'est le coach lui-même qui les édite. Les classer runtime, c'est faire geler la
#     synchronisation du vault par une écriture que le coach a le droit de faire.
#   - `server/tests/` : jamais importé par le service. Un commit qui touche un test ET
#     du code est de toute façon retenu par le code.
RUNTIME_PATHS = (
    "server/",
    "scripts/",
    "migrations/",
    ".github/",
    ".githooks/",
    "alembic.ini",
    "pyproject.toml",
)

#: Les arbres dont l'empreinte identifie le code déployé (`/health`, release).
RUNTIME_TREES = ("server", "scripts", "migrations")

#: Exceptions à `RUNTIME_PATHS` : sous un préfixe runtime, mais sans effet à l'exécution.
RUNTIME_EXEMPT = (
    "server/tests/",
)

# Causes d'alerte technique. La clé évite de dupliquer une cause pendant un même
# passage de synchronisation ou de persistance.
ALERT_DEPLOY = "deploiement"
ALERT_HISTORY = "historique"
ALERT_PUSH = "push"
ALERT_COMMIT = "commit"
ALERT_PROJECTION = "projection"
ALERT_WRITE = "ecriture"


def needs_deployment(path: str) -> bool:
    """Whether integrating this file would change how the running service behaves."""
    return path.startswith(RUNTIME_PATHS) and not path.startswith(RUNTIME_EXEMPT)

CANONICAL_PROJECTIONS = frozenset(str(path) for path in vault_layout.PROJECTIONS)


@dataclass(frozen=True)
class VaultAlert:
    """Un incident interne et sa cause, journalisés au plus une fois avant drainage.
    """
    key: str
    text: str


class PersistenceFailure(enum.Enum):
    """Pourquoi les faits d'un tour n'ont pas été enregistrés.

    Les fichiers interdits (projection éditée à la main, code modifié) ne refusent plus
    le tour : ils sont restaurés et le reste continue. Seul l'échec d'application des
    commandes elles-mêmes laisse des faits non enregistrés.
    """

    #: Les faits n'ont pas pu être appliqués (schéma, immuabilité, conflit, panne).
    WRITE_REFUSED = "écriture refusée"


@dataclass(frozen=True)
class PersistenceResult:
    writes_applied: bool
    detail: str = ""
    # Semaines enregistrées mais non prescriptibles tant qu'un contrôle daté tient.
    awaiting: tuple = field(default_factory=tuple)
    # Semaines enregistrées mais périmées : une séance a bougé depuis leur calcul.
    stale: tuple = field(default_factory=tuple)
    # Semaines absentes ou invalides : aucun bloc de séances ne peut partir.
    invalid: tuple = field(default_factory=tuple)
    # La cause typée, quand les écritures sont refusées.
    failure: PersistenceFailure | None = None
    applied_commands: int = 0
    receipts: tuple[dict, ...] = ()
    # Fichiers interdits que le tour avait touchés et qui ont été remis en état.
    restored: tuple[str, ...] = ()
    # Types des commandes qui n'ont pas été enregistrées (écriture refusée).
    refused: tuple[str, ...] = ()


def _transient(error: StorageError) -> bool:
    """Une panne de connexion ou de verrou se réessaie ; un refus métier jamais."""
    cause = error.__cause__
    return isinstance(cause, (sa_exc.OperationalError, sa_exc.InterfaceError)) or bool(
        getattr(cause, "connection_invalidated", False))


def forbidden_in_turn(path: str) -> bool:
    """Ce qu'un tour coach n'a jamais le droit de modifier dans le vault."""
    return (path in CANONICAL_PROJECTIONS or path.startswith(f"{vault_layout.CAPABILITIES}/")
            or needs_deployment(path))


class VaultRepo:
    def __init__(self, path: str, remote: str, branch: str,
                 operational_store: OperationalStore | None = None) -> None:
        self._path = path
        self._remote = remote
        self._branch = branch
        self._alerts: list[VaultAlert] = []
        self._store = operational_store
        # Un commit local attend son push, des vues attendent leur régénération
        # (mémoire seulement : un redémarrage retrouve le premier par
        # `_ahead_of_remote`, le second par l'état des projections PostgreSQL).
        self._push_due = False
        self._views_due = False

    def _alert(self, key: str, text: str) -> None:
        """File un incident, une seule fois par cause tant qu'il n'est pas drainé.

        `_push()` rejoue `sync()` avant de réessayer : sans cette garde, un dépôt qui
        attend un déploiement produisait DEUX fois le même texto dans le même tour
        (reçu par l'athlète le 2026-08-21).
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
        """L'empreinte du CODE à ``ref`` : elle ne bouge pas quand le coach committe.

        HEAD avance à chaque tour qui écrit une note ; il ne dit pas quel code tourne.
        `server/verify-release.sh` recalcule la même empreinte depuis le SHA déployé.
        """
        trees = [self._git("rev-parse", f"{ref}:{path}", log_errors=False).stdout.strip()
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
            # L'agent committe parfois lui-même ; un tour refusé ne garde pas ce commit
            # (jamais poussé : le push ne vit que derrière une persistance réussie).
            log.critical("Tour abandonné après un commit de l'agent : retour à %s",
                         base_head[:8])
            self._git("reset", "--hard", base_head)
        self._reset_worktree()

    def discard_turn(self, turn_id: str) -> None:
        if self._store is not None:
            self._store.discard_turn(turn_id)

    def clear_turn_inputs(self, turn_id: str) -> None:
        """Supprime les JSON remis aux CLI par ce tour (ignorés par Git)."""
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
        """`clean -fd` sans `-x` : les fichiers ignorés survivent, dont `server/outbox/`
        dont les images sont encore en attente d'archivage."""
        self._git("reset", "--hard", "HEAD")
        self._git("clean", "-fd")

    def _changed_paths(self) -> list[str]:
        tracked = self._git("diff", "--name-only", "HEAD", log_errors=False).stdout.splitlines()
        untracked = self._git(
            "ls-files", "--others", "--exclude-standard", log_errors=False).stdout.splitlines()
        return list(dict.fromkeys([*tracked, *untracked]))

    def persist_turn(self, required_weeks=(), base_head: str = "",
                     turn_id: str = "") -> PersistenceResult:
        """Enregistre un tour AVANT sa livraison, sans aucun appel réseau.

        1. Un fichier interdit (projection PostgreSQL éditée à la main, code, capacités)
           est restauré : c'est cet effet-là qui est dangereux, pas le reste du tour.
        2. Les commandes préparées, déjà validées pendant le tour, sont appliquées dans
           PostgreSQL, avec une reprise courte sur panne transitoire.
        3. Les notes éditées par le coach sont committées localement, sans push.
        4. Les semaines requises et celles que le tour a révisées sont relues.

        Projections, push et synchronisation viennent après la livraison (`publish`).
        ``required_weeks`` est vide pour un tour social ; une semaine dont un contrôle
        daté est échu s'enregistre et ressort dans ``awaiting``.
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
        # Les semaines réellement révisées se contrôlent même si le triage n'a
        # pas identifié de changement de programme.
        required_weeks = tuple(dict.fromkeys((*required_weeks, *(
            receipt["payload"]["week"] for receipt in receipts
            if receipt["kind"] == "plan.revise"))))
        if self._commit_local() or applied:
            self._views_due = True
        try:
            statuses = {week: self.validate_weekly_plan(week) for week in required_weeks}
        except Exception as exc:
            # La transaction PostgreSQL est déjà confirmée. Une relecture impossible ne
            # peut pas la transformer en écriture refusée ; elle retient seulement les
            # séances, faute de pouvoir les prouver.
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
        """Remet les fichiers interdits dans leur état d'avant le tour."""
        current = self._head()
        if base_head and current and current != base_head:
            # L'agent a committé lui-même : ses changements redeviennent du travail en
            # cours, jugés comme le reste, puis committés par le service.
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
        """Committe les notes du coach sans hook ni réseau ; rend True si un commit naît."""
        paths = self._changed_paths()
        if not paths:
            return False
        staged = self._git("add", "-A", "--", *paths)
        if staged.returncode != 0:
            detail = (staged.stderr or staged.stdout).strip()[:200]
            self._alert(ALERT_COMMIT, f"Préparation du commit refusée par git : {detail}")
            return False
        stamp = clock.now().strftime("%Y-%m-%d %H:%M")
        # Cette frontière scelle et régénère elle-même ; le hook sert aux commits humains.
        committed = self._git("commit", "--no-verify", "-m", f"coach (messenger) - {stamp}")
        if committed.returncode != 0:
            detail = (committed.stderr or committed.stdout).strip()[:200]
            self._alert(ALERT_COMMIT, f"Commit du vault refusé par git : {detail}")
            return False
        self._push_due = True
        return True

    def publish(self) -> bool:
        """Après la livraison : vues, commit et push, hors du temps de réponse.

        Git et les vues sont en cohérence éventuelle ; PostgreSQL a déjà tout. Rend
        True quand rien ne reste à publier.
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
        """(encore réclamables, sorties de leur fenêtre) sur UNE lecture du registre."""
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
        """Toutes les séances prévues ce jour-là sont soldées : faites, manquées ou annulées.

        Un repos ne s'attend pas. Sans programme lisible, rien n'est soldé.
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
        """Faits bornés déjà disponibles, avant que l'agent décide d'élargir."""
        if self._store is None:
            return ""
        return render_context_packet(self._store, depth, clock.today())

    def weekly_dossier(self, week: str) -> str:
        """Le dossier calculé du bilan de `week` ; une panne se dit, elle ne bloque rien.

        Le bilan doit pouvoir se faire même si un calcul échoue : le modèle relancera
        alors les CLI lui-même, comme avant le 2026-09-27.
        """
        if self._store is None:
            return ""
        from pathlib import Path

        from scripts import weekly_dossier
        try:
            return weekly_dossier.render(self._store, Path(self._path), week, clock.today())
        except Exception:  # le dossier est une aide, jamais une porte
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
        script = os.path.join(self._path, "scripts", "chart-plan.py")
        outbox = os.path.join(self._path, "server", "outbox")
        os.makedirs(outbox, exist_ok=True)
        target = os.path.join(outbox, f"plan-{week.lower()}-r{revision}.png")
        command = [sys.executable, script, "--week", week, "-o", target]
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
        """Rend la carte des répétitions d'une séance de qualité dans l'outbox."""
        script = os.path.join(self._path, "scripts", "chart-workout.py")
        outbox = os.path.join(self._path, "server", "outbox")
        os.makedirs(outbox, exist_ok=True)
        target = os.path.join(outbox, f"workout-{activity_id}.png")
        try:
            process = subprocess.run(
                [sys.executable, script, "-o", target], cwd=self._path,
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

        from scripts.storage.projector import project_all
        if self._store is not None:
            project_all(self._store, Path(self._path), finalize=self._regenerate_static_views)
        else:
            self._regenerate_static_views()

    def _regenerate_static_views(self) -> None:
        script = os.path.join(self._path, REGEN)
        if not os.path.exists(script):  # a clone that is not the vault (tests)
            return
        process = subprocess.run(
            [sys.executable, script], cwd=self._path, capture_output=True,
            text=True, timeout=PROCESS_TIMEOUT)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip()[:300]
            log.error("régénération des vues échouée (%d) : %s",
                      process.returncode, detail)
            raise StorageError(
                "régénération des vues du vault échouée : " + detail)

    def _push(self) -> bool:
        """Pousser, et n'annoncer qu'UN incident : celui qui dit quoi faire.

        Un push refusé pendant qu'un déploiement est en attente n'est pas un second
        incident, c'est la CONSÉQUENCE du premier — le distant est en avance de commits
        que `sync()` a refusé d'intégrer. Les annoncer séparément donnait deux textos
        dont aucun ne nommait l'action à faire.
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

    def _git(self, *args: str, log_errors: bool = True) -> subprocess.CompletedProcess:
        command = ["git", *args]
        try:
            process = subprocess.run(
                command, cwd=self._path, capture_output=True, text=True,
                timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            process = subprocess.CompletedProcess(
                command, 124, "", f"timeout après {GIT_TIMEOUT} s")
        if log_errors and process.returncode != 0:
            detail = (process.stderr or process.stdout).strip()[:300]
            log.error("git %s failed (%d): %s", args[0], process.returncode, detail)
        return process
