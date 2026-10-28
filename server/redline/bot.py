"""CoachBot: the orchestrator. A single worker serializes everything (reactive +
proactive), so git writes and agent turns never step on each other, with a short
debounce that groups texts sent in a burst into a single run.

Un tour réactif, c'est un appel de modèle (plus un triage court) : la réponse du
modèle part telle quelle. Le service n'y ajoute que ce qu'il sait de façon
déterministe (bloc de séances, limite précise), il ne la remplace jamais. Toute sortie
calculée est conservée avant l'envoi : une reprise la renvoie, elle ne la recalcule pas.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

from scripts.model import commands as command_model
from scripts.model import ledger as ledger_model
from scripts.model import session_kinds
from scripts.model import weekly_plan as weekly_plan_model
from scripts.storage import StorageError

from . import clock, events
from .agent import AgentRunner, FailureKind, SessionMode, Triage
from .attachments import AttachmentError, AttachmentService
from .config import Config
from .delivery import (
    DeliveryReceipt,
    DeliveryService,
    EnvelopeBuilder,
    ImagePart,
    OutboundEnvelope,
)
from .messenger import MessengerClient, TypingIndicator
from .notebook import NotebookService
from .outbox import Outbox, OutboxSnapshot
from .plans import (
    PlanStatus,
    blocked_session_ids,
    due_gates_by_kind,
    prescription_lines,
    prescription_state,
    render_plan_delta,
    render_plan_for_scope,
    render_plan_message,
    strength_due,
)
from .proactive import ACTIVE_SILENCE_SECONDS, ProactiveJob, ProactiveScheduler
from .proactive import HEAVY_KINDS as HEAVY_PROACTIVE_KINDS
from .proactive_messages import (
    CLOSED_KINDS,
    ClosedProactive,
    compose_closed_proactive,
    missing_cells_in_notebook,
)
from .prompts import (
    BATTERY_NOTE,
    CARNET_NOTE,
    CELLS_UNREADABLE_NOTE,
    CLASSIFY_PREAMBLE,
    DEPTH_NOTES,
    EMOTION_NOTE,
    HELP_TEXT,
    MUST_SPEAK_KINDS,
    PROACTIVE_TRIGGERS,
    STALE_VAULT_NOTE,
    STRENGTH_REPORT_NOTE,
    active_revision_note,
    attachment_prompt,
    canonical_plan_note,
    debrief_in_reply,
    deep_handoff_note,
    gate_note,
    missing_cells_note,
    plan_change_note,
    stale_plan_note,
    strava_snapshot_note,
    timing_note,
    today_note,
)
from .routing import Route
from .state import StateStore
from .strength_artifact import render_strength_artifact
from .textutils import burst_parts, strip_links
from .triage_rules import deterministic_triage
from .turns import TurnExecutor
from .vault import PersistenceResult, VaultRepo
from .work_queue import (
    AttachmentRequest,
    CommandRequest,
    DeepRequest,
    InboundRequest,
    MaintenanceRequest,
    WorkQueue,
)

log = logging.getLogger("redline")

# Les déclencheurs pour lesquels se taire n'est PAS une option éditoriale : ils partent
# d'une activité qui vient d'arriver, donc il y a toujours matière. Leur silence est une
# panne de lecture et se reprend une fois. Les autres (mot du matin, prise de
# nouvelles) ont le droit de n'avoir rien à dire, et c'est même la bonne réponse.
SILENCE_IS_A_FAILURE = frozenset({"strava_debrief", "strava_debrief_differe"})
CONTROL_COMMANDS = frozenset({"/status", "/cancel"})
# Niveaux dont le paquet métier porte déjà les fils ouverts (`context_packet.py`).
PACKET_THREAD_DEPTHS = frozenset({"N2", "N3"})

# ⭐ UNE SEULE VOIX POUR LES LIMITES. Aucune phrase n'est fabriquée par un adaptateur :
# elles disent à l'athlète ce qui se passe POUR LUI et ce qui va arriver, jamais le
# mécanisme (« watchdog », « tour », « contrôle »). Classe 2 de la doctrine d'erreur :
# une phrase précise, une fois, seulement quand il attend quelque chose qui ne viendra
# pas dans un délai humain ou qu'une donnée qu'il a donnée n'est pas enregistrée.
FAILURE_REPLIES = {
    FailureKind.WATCHDOG: (
        "Je n'ai pas réussi à finir ma réponse à temps. Redis-moi l'essentiel en une "
        "phrase et je repars de là."),
    FailureKind.IN_FLIGHT: (
        "Ma réponse s'est interrompue en route. Renvoie-moi ton message quand tu veux, "
        "je reprends de zéro."),
}
OUTAGE_NOTICE = (
    "Je n'arrive pas à te répondre tout de suite. Je garde ton message et je te réponds "
    "dès que possible.")
EMPTY_REPLY = "Je n'ai pas réussi à formuler ma réponse. Redis-moi l'essentiel et je repars de là."
EMPTY_REPLY_APPLIED = (
    "J'ai bien noté ce que tu m'as donné, mais ma réponse ne s'est pas écrite. Dis-moi si "
    "tu attendais autre chose.")
PLAN_UNAVAILABLE_LINE = (
    "Je n'ai pas de programme valide pour cette semaine en ce moment, donc pas de séance "
    "à te donner tant qu'il n'est pas refait. Je m'en occupe.")
ATTACHMENT_UNREADABLE = (
    "Je n'ai pas réussi à lire ce fichier en entier. Envoie-moi seulement la page ou "
    "l'image qui compte.")
UNRECORDED_GENERIC = (
    "Attention : ce que tu viens de me donner n'a pas pu être enregistré. Ne le considère "
    "pas comme noté ; redis-le-moi dans un moment.")
# Ce que l'athlète a donné, nommé dans ses mots. Les commandes internes (question déclarée,
# angle, fil) n'apparaissent pas : leur perte ne change rien pour lui.
UNRECORDED_LABELS = {
    command_model.OBSERVATION_ADD: "ta séance",
    command_model.EXPOSURE_UPSERT: "ta séance",
    command_model.MEASUREMENT_COMPLETE: "tes ressentis sur la séance",
    command_model.HR_DOUBT: "ta FC douteuse",
    command_model.PLAN_REVISE: "le changement de programme",
    command_model.DAILY_UPSERT: "ton état du jour",
    command_model.SESSION_START: "ton départ",
    command_model.SESSION_MATCH: "le rattachement de ta sortie",
    command_model.ACTIVITY_LINK: "le rattachement de ta sortie",
    command_model.GEAR_UPSERT: "ton matériel",
}


def unrecorded_line(kinds: Iterable[str]) -> str:
    """La phrase qui nomme ce qui n'a pas été enregistré, ou rien si l'athlète n'y perd rien."""
    kinds = tuple(kinds)
    if not kinds:
        return UNRECORDED_GENERIC
    labels = list(dict.fromkeys(UNRECORDED_LABELS[kind] for kind in kinds
                                if kind in UNRECORDED_LABELS))
    if not labels:
        return ""
    joined = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " et " + labels[-1]
    return (f"Attention : je n'ai pas pu enregistrer {joined}. Ne le considère pas comme "
            "noté ; redis-le-moi dans un moment.")


#: Les initiatives qui demandaient des ressentis que le carnet recueille désormais.
CARNET_KINDS = frozenset({"strava_debrief", "strava_debrief_differe", "morning"})


class CoachBot:
    HEAVY_KINDS = HEAVY_PROACTIVE_KINDS
    PATIENT_DEPTHS = frozenset({"N2", "N3"})
    PATIENCE_MIN_GAP = 10 * 60
    # Un entrant direct a déjà les replis modèle de sa route et les tentatives réseau
    # de chaque adaptateur. Ces reprises espacées couvrent ensuite une indisponibilité
    # plus longue ; au-delà de la troisième, elles continuent toutes les 30 min.
    REACTIVE_RETRY_DELAYS: ClassVar[tuple[float, ...]] = (30.0, 300.0, 1800.0)
    # Environ 24 h de reprises : au-delà, le message reste `pending` et un redémarrage
    # le reprendra, avec une note d'âge.
    REACTIVE_MAX_ATTEMPTS = 48
    # Une reprise plus lointaine que ça mérite UNE phrase : l'athlète attend.
    OUTAGE_NOTICE_AFTER = 120.0
    OUTAGE_NOTICE_GAP = 2 * 3600
    # Reprises d'une initiative : même cause ou non, trois réclamations au plus.
    PROACTIVE_RETRY_DELAYS: ClassVar[tuple[float, ...]] = (300.0, 900.0, 2700.0)
    PROACTIVE_MAX_ATTEMPTS = 3
    WEEK_REPAIR_CHECK_SECONDS = 15 * 60
    # How long a proactive stays relevant after its first failed attempt: past that
    # we drop it rather than replay it (a "good morning" in the afternoon is noise).
    RETRY_STALENESS_HOURS: ClassVar[dict] = {
        "strava_debrief": 4.0,
        # Already a next-morning message: if the agent is down at 07:30 it still makes
        # sense at 10:00, not the day after.
        "strava_debrief_differe": 4.0,
        "morning": 3.0,
        "weekly": 6.0,
        "weekly_repair": 12.0,
        "monthly": 24.0,
        "silence": 24.0,
        # La cellule sort de sa fenêtre à minuit : une réclamation rejouée le lendemain
        # ne demande plus un souvenir mais une reconstruction. Elle meurt avec la nuit.
        "registre_incomplet": 2.0,
        "missed_session": 8.0,
        "daily_signal": 12.0,
        "premortem": 24.0,
        "correction": 24.0,
        "decision_challenge": 24.0,
        "gut_review": 12.0,
        "gear_alert": 24.0,
        "race_countdown": 168.0,
        "strength_card": 12.0,
        "operator_message": 6.0,
        # Le carnet ne prend plus la trace après 14 h : `expires_at` borne déjà le job.
        "trace_reveil": 3.5,
    }

    def __init__(self, client: MessengerClient, repo: VaultRepo,
                 agent: AgentRunner, state: StateStore, config: Config,
                 outbox: Outbox, attachments: AttachmentService | None = None,
                 notebook: NotebookService | None = None) -> None:
        self._client = client
        self._repo = repo
        self._agent = agent
        self._state = state
        self._config = config
        self._outbox = outbox
        self._attachments = attachments or AttachmentService(
            Path(config.attachment_dir), max_bytes=config.attachment_max_bytes,
            total_bytes=config.attachment_total_bytes)
        self._delivery = DeliveryService(client, outbox, state, config.coach_base_url, notebook)
        self._turns = TurnExecutor(agent, repo, state)
        self._queue = WorkQueue(config.debounce_seconds, config.debounce_max_seconds)
        self._thread: threading.Thread | None = None
        # État du seul worker, relu par le planificateur : une conversation est active
        # pendant un tour réactif et dans les minutes qui suivent sa réponse.
        self._reactive_since: float | None = None
        self._last_reactive_reply = 0.0
        self._outage_notice_at = 0.0
        self._retry_not_before: float | None = None
        self._publish_due = False
        self._vault_moved = False
        self._week_repair_checked = 0.0

    def start(self) -> None:
        self.reconcile_outbox()
        removed = self._attachments.cleanup()
        if removed:
            log.warning("Pièces jointes brutes retirées après redémarrage : %d", removed)
        if self._thread is not None and self._thread.is_alive():
            return
        self._queue.open()
        for message in self._state.pending_inbound():
            self.submit_event(message["psid"], message["kind"], message["body"],
                              message_id=message["message_id"])
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="coach-worker")
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        """Stop accepting work, close the model process and join the worker."""
        self._queue.close()
        self._agent.close()
        if self._thread is not None:
            self._thread.join(timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def reconcile_outbox(self) -> int:
        """Archive crash leftovers and report them instead of sending stale cards."""
        leftovers = self._outbox.leftovers()
        if not leftovers:
            return 0
        names = [path.name for path in leftovers]
        log.error("Outbox : %d image(s) laissée(s) par un tour mort, jamais envoyée(s) "
                  "— %s", len(names), ", ".join(names))
        for path in leftovers:
            self._outbox.archive(path)
        return len(names)

    @property
    def repo(self) -> VaultRepo:
        """Exposed for /health, which reports the code the process booted on."""
        return self._repo

    # ---- inputs

    def submit_event(self, psid: str, kind: str, text: str, *, message_id: str) -> None:
        """Dispatch one durably claimed Messenger event."""
        if kind in {"text", "postback"}:
            self.submit_inbound(psid, text, message_id=message_id)
            return
        if kind == "sticker":
            self.acknowledge(psid)
        elif kind == "attachment":
            self.submit_attachment(psid, text, message_id=message_id)
            return
        else:
            raise ValueError(f"type d'événement Messenger inconnu : {kind}")
        self._state.complete_inbound((message_id,))

    def submit_inbound(self, psid: str, text: str, *, message_id: str = "") -> None:
        """Text message from the athlete; control commands bypass a busy worker."""
        command = text.strip()
        if command in CONTROL_COMMANDS:
            ids = (message_id,) if message_id else ()
            threading.Thread(
                target=self._run_control_command, args=(psid, command, ids),
                daemon=True, name=f"control-{command[1:]}").start()
            return
        abandoned = self._queue.submit_inbound(psid, text, message_id)
        self._state.complete_inbound(abandoned)

    def _run_control_command(self, psid: str, command: str,
                             message_ids: tuple[str, ...]) -> None:
        """Livre status/cancel pendant que le worker principal attend le modèle."""
        try:
            processed = self._run_command(psid, command, message_ids=message_ids)
        except Exception:
            log.exception("Commande de contrôle %s impossible", command)
            return
        if processed:
            self._state.complete_inbound(message_ids)

    def submit_attachment(self, psid: str, payload: str, *, message_id: str) -> None:
        """La lecture se fait dans le worker ; le webhook reste immédiatement libre."""
        self._queue.submit_attachment(psid, payload, message_id)

    def acknowledge(self, psid: str) -> None:
        """Sticker / thumbs-up: marking as seen is enough, a human does not reply."""
        self._client.action(psid, "mark_seen")

    def submit_proactive(self, job: ProactiveJob) -> None:
        self._queue.submit_proactive(job)

    def request_maintenance(self) -> None:
        """Publication Git et synchronisation, quand le worker n'a rien d'autre à faire."""
        self._queue.submit_maintenance()

    def has_pending_inbound(self) -> bool:
        return self._queue.has_pending_inbound()

    def conversation_active(self, psid: str) -> bool:
        """L'athlète converse : message en file, tour en vol, ou échange récent.

        La sortie du coach compte autant que l'entrée de l'athlète : un tour de douze
        minutes laissait passer une relance juste derrière sa propre réponse.
        """
        now = time.time()
        return (self.has_pending_inbound() or self._reactive_since is not None
                or now - self._state.last_inbound(psid) < ACTIVE_SILENCE_SECONDS
                or now - self._last_reactive_reply < ACTIVE_SILENCE_SECONDS)

    # ---- worker

    def _worker(self) -> None:
        while True:
            job = self._queue.next()
            if job is None:
                return
            if isinstance(job, MaintenanceRequest):
                self._maintain()
                continue
            processed = True
            if not isinstance(job, ProactiveJob):
                self._reactive_since = time.time()
            try:
                if isinstance(job, ProactiveJob):
                    self._respond_proactive(job)
                elif isinstance(job, CommandRequest):
                    processed = self._run_command(
                        job.psid, job.command, message_ids=job.message_ids)
                elif isinstance(job, DeepRequest):
                    processed = self._respond(
                        job.psid, job.text, deep_request=True,
                        message_ids=job.message_ids)
                elif isinstance(job, AttachmentRequest):
                    processed = self._respond_attachment(job)
                else:
                    processed = self._respond(
                        job.psid, job.text, message_ids=job.message_ids)
            except Exception:
                log.exception("Failed to handle a message")
                if isinstance(job, ProactiveJob):
                    try:
                        self._buffer_proactive_retry(job, cause="exception du service")
                    except Exception:
                        log.exception("Reprise du proactif %s impossible", job.job_id)
                else:
                    # Une panne du worker n'est jamais une action à demander à
                    # l'athlète. Le message entrant est durable et sera repris.
                    self._log_incident("worker", "message entrant non traité")
                    processed = False
            finally:
                self._reactive_since = None
            try:
                if not processed and not isinstance(job, ProactiveJob):
                    self._retry_inbound(job)
                elif isinstance(job, (InboundRequest, DeepRequest, CommandRequest)):
                    self._state.complete_inbound(job.message_ids)
                elif isinstance(job, AttachmentRequest):
                    self._state.complete_inbound((job.message_id,))
            except Exception:
                # L'entrant reste `pending` : sa réponse, elle, est déjà conservée et
                # ne sera jamais recalculée à la reprise.
                log.exception("Acquittement de l'entrant impossible")
            if self._publish_due:
                self._after_turn()

    def _after_turn(self) -> None:
        """Git et vues après la livraison : ils ne retardent jamais une réponse."""
        self._publish_due = False
        try:
            self._repo.publish()
            self._vault_moved = bool(self._repo.sync()) or self._vault_moved
        except Exception:
            log.exception("Publication du vault interrompue ; reprise à la maintenance")
        self._log_vault_alerts()

    def _maintain(self) -> None:
        """Maintenance à l'arrêt : publication, synchronisation, questions périmées."""
        self._after_turn()
        psid = self._config.allowed_psid
        if psid:
            try:
                expired = self._state.expire_stale_questions(psid)
                if expired:
                    log.info("Questions ouvertes fermées sans examen : %d", expired)
            except Exception:
                log.exception("Expiration des questions ouvertes impossible")

    def _take_vault_moved(self) -> bool:
        moved, self._vault_moved = self._vault_moved, False
        return moved

    @staticmethod
    def _job_ids(job) -> tuple[str, ...]:
        if isinstance(job, AttachmentRequest):
            return (job.message_id,)
        return tuple(getattr(job, "message_ids", ()))

    def _retry_inbound(self, job) -> None:
        """Retente un entrant sans le perdre ; dit UNE fois à l'athlète qu'il attend."""
        attempt = max(1, int(getattr(job, "attempts", 1)))
        hint, self._retry_not_before = self._retry_not_before, None
        ids = self._job_ids(job)
        if attempt >= self.REACTIVE_MAX_ATTEMPTS:
            # Ne pas acquitter : PostgreSQL le garde ``pending`` et un redémarrage le
            # reprendra, avec une note d'âge. On évite à la fois la perte et la boucle.
            self._log_incident(
                "entrant-en-attente", f"message conservé après {attempt} tentatives")
            return
        delay = self.REACTIVE_RETRY_DELAYS[min(attempt, len(self.REACTIVE_RETRY_DELAYS)) - 1]
        if hint:
            # Le fournisseur sait quand il revient : inutile de frapper avant.
            delay = min(max(delay, hint - time.time()), 6 * 3600)
        if delay >= self.OUTAGE_NOTICE_AFTER and not isinstance(job, CommandRequest):
            self._notify_outage(job.psid, ids)

        def enqueue() -> None:
            self._queue.retry(job)

        timer = threading.Timer(delay, enqueue)
        timer.daemon = True
        timer.start()
        events.emit("inbound.retry", inbound_ids=list(ids), attempt=attempt,
                    retry_in_s=int(delay))
        log.warning("Entrant direct différé après échec : tentative %d dans %.0f s",
                    attempt + 1, delay)

    def _notify_outage(self, psid: str, ids: tuple[str, ...]) -> None:
        """Une seule phrase par épisode de panne, jamais le mécanisme."""
        if time.time() - self._outage_notice_at < self.OUTAGE_NOTICE_GAP:
            return
        self._outage_notice_at = time.time()
        self._deliver_limit(psid, "outage", ids, OUTAGE_NOTICE)

    # ---- replies

    def _deliver_attachment_text(self, request: AttachmentRequest, text: str) -> bool:
        source_key = f"attachment:{request.message_id}"
        envelope = (EnvelopeBuilder(request.psid, source_key)
                    .meta(kind="attachment", memory=text).text(text).build())
        return self._finish_reactive(request.psid, source_key, dict(envelope.meta),
                                     self._delivery.deliver(envelope))

    def _respond_attachment(self, request: AttachmentRequest) -> bool:
        """Lit le brut sans lui donner de chemin d'écriture, puis le détruit."""
        self._client.action(request.psid, "mark_seen")
        source_key = f"attachment:{request.message_id}"
        resumed = self._resume_reactive(request.psid, source_key)
        if resumed is not None:
            return resumed
        turn_id = str(uuid.uuid4())
        try:
            with self._attachments.materialize(
                    request.payload, request.message_id) as batch:
                caption = self._attachments.caption(request.payload)
                with TypingIndicator(
                        self._client, request.psid, deep=True,
                        gate=lambda: self._state.claim_patience(self.PATIENCE_MIN_GAP),
                        deliver=lambda line: self._deliver_holding(request.psid, line)):
                    memory = self._state.relationship_context(request.psid)
                    prompt = attachment_prompt(batch.files, caption,
                                               ignored_video=batch.ignored_video)
                    if memory:
                        prompt = memory + "\n\n" + prompt
                    result = self._turns.read_only(
                        request.psid, prompt,
                        plan=self._config.routing.plan(Route.N1).with_read_only(),
                        turn_id=turn_id,
                    )
        except AttachmentError as exc:
            return self._deliver_attachment_text(request, str(exc))
        if result.error:
            self._log_incident(
                "analyse-piece-jointe",
                f"La pièce jointe a bien été supprimée, mais son analyse a échoué "
                f"({result.failure}) : {result.error}")
            if not result.terminal_failure:
                self._retry_not_before = result.retry_at
                return False
            if result.failure is FailureKind.CANCELLED:
                return True
            # Une analyse qui a déjà échoué en cours de route ne se relance pas à
            # l'aveugle : l'athlète sait quoi renvoyer.
            return self._deliver_attachment_text(request, ATTACHMENT_UNREADABLE)
        answer = result.full_text.strip() or (
            "Je n'arrive pas à extraire quelque chose de fiable de cette pièce jointe.")
        return self._deliver_attachment_text(request, answer)

    def _with_freshness(self, psid: str, prompt: str, vault_changed: bool) -> str:
        """If a sync brought new commits while a model session is still alive, prepend
        a note asking to re-read the hot cache: the session's context was read at
        conversation start and may predate those commits. A fresh session re-reads
        the vault anyway, no note needed."""
        has_live_session = (self._state.session_id(psid, self._agent.provider) is not None
                            and self._state.session_age(psid, self._agent.provider)
                            <= self._config.session_ttl)
        if vault_changed and has_live_session:
            log.info("Vault moved during a live session: asking the agent to re-read the hot cache")
            return STALE_VAULT_NOTE + "\n\n" + prompt
        return prompt

    def pending_ledger_cells(self, today) -> list:
        """Ce que le registre attend encore de l'athlète. Lu par le planificateur pour
        son repli du soir : le chemin normal est la greffe sur un message existant."""
        return self._repo.pending_ledger_cells(today)

    def _missing_cells_note(self, today, psid: str = "") -> str:
        """Liste les inconnues sans présumer quelles questions seront posées."""
        try:
            pending, lapsed = self._repo.ledger_cells(today)
            pro = self._state.proactive
            asked = [entry for entry in lapsed
                     if any(pro.marked("asked:" + ledger_model.cell_key(entry, column))
                            for column in entry["missing"])]
        except StorageError:
            log.warning("Lecture des ressentis manquants indisponible")
            return CELLS_UNREADABLE_NOTE
        return missing_cells_note(
            pending, asked, carnet=bool(psid) and self._notebook_connected(psid))

    def _notebook_connected(self, psid: str) -> bool:
        """Le carnet recueille les ressentis : Messenger ne les redemande plus."""
        notebook = self._delivery.notebook
        return notebook is not None and notebook.connected(psid)

    def day_settled(self, day) -> bool:
        """Lu par le planificateur le dimanche : le bilan peut partir sans attendre 19 h."""
        try:
            return self._repo.day_settled(day)
        except StorageError:
            log.warning("Lecture des séances du jour indisponible ; le bilan garde son heure")
            return False

    def wake_reminder_due(self, psid: str) -> bool:
        """Lu par le planificateur à 10 h 30 : la trace du réveil manque encore."""
        notebook = self._delivery.notebook
        return (notebook is not None and notebook.connected(psid)
                and notebook.wake_reminder_due())

    def _mark_cells_asked(self, keys) -> None:
        """« Demandé » veut dire qu'un message est parti, pas qu'un prompt a été assemblé."""
        for key in keys:
            self._state.proactive.mark("asked:" + key)

    def _reading_notes(self, triage: Triage, week: str, plan_context: str, *,
                       plan_required: bool, handoff: str, psid: str = "") -> str:
        """Tout ce que le service met DEVANT le message de l'athlète, et rien d'autre.

        Cinq blocs invisibles, chacun avec sa propre raison d'être là : le niveau de
        lecture, l'état d'esprit, le programme canonique (plus sa réparation s'il est
        périmé), les contrôles datés du jour, les cellules du registre à réclamer, et
        la passerelle d'un `/deep` précédent.

        Cette lecture ne marque aucune cellule comme demandée.
        """
        # L'ancre temporelle passe AVANT le niveau de lecture : « aujourd'hui repos »
        # arrive en N0 comme en N2, et c'est en N0 que le coach lit le moins.
        notes = today_note(clock.now()) + "\n\n" + DEPTH_NOTES.get(triage.depth, "")
        if triage.emotional:
            notes = EMOTION_NOTE + "\n\n" + notes
        if triage.plan_change:
            notes += "\n\n" + plan_change_note()
        if plan_required:
            notes += "\n\n" + canonical_plan_note(plan_context)
            if plan_context and self._repo.validate_weekly_plan(week) is PlanStatus.STALE:
                notes += "\n\n" + stale_plan_note(week)
        # Un contrôle bloquant voyage à tous les niveaux. Une observation, elle, ne
        # force pas un échange social ou une clôture à changer de sujet.
        gates = gate_note(*due_gates_by_kind(
            plan_context, clock.today().isoformat()), closing=triage.depth == "N0")
        if gates:
            notes += "\n\n" + gates
        cells = (self._missing_cells_note(clock.today(), psid)
                 if triage.depth != "N0" and not triage.emotional else "")
        if cells:
            notes += "\n\n" + cells
        if handoff:
            notes += "\n\n" + deep_handoff_note(handoff)
        return notes

    def _triage(self, psid: str, text: str, message_ids: tuple[str, ...], *,
                strength_report: bool) -> Triage:
        if strength_report:
            # La route a déjà validé et écrit la séance avant de créer ce message.
            # Son identité rend le classifieur inutile.
            return Triage("N2")
        # Le triage sélectionne le niveau de lecture et le ton. Il ne décide d'aucune
        # porte : un verdict absent retombe sur le défaut, qui ne verrouille rien.
        triage = deterministic_triage(text)
        if triage is not None:
            return triage
        try:
            memory = self._state.relationship_context(psid)
            reply_to = self._state.reply_context(message_ids)
        except Exception:
            log.exception("Contexte du triage indisponible")
            memory, reply_to = "", ""
        classification = (
            CLASSIFY_PREAMBLE
            + json.dumps({"message": text, "recent_context": memory,
                          "reply_to": reply_to}, ensure_ascii=False))
        return self._agent.classify(
            classification, spec=self._config.routing.plan(Route.TRIAGE).primary)

    def _timing_note(self, message_ids: tuple[str, ...]) -> str:
        try:
            received = self._state.inbound_received_at(message_ids)
        except Exception:
            log.exception("Heure de réception des messages illisible")
            return ""
        if not received:
            return ""
        return timing_note(time.time() - received, clock.fromtimestamp(received),
                           overtaken=received < self._last_reactive_reply)

    @staticmethod
    def _reactive_key(message_ids: tuple[str, ...]) -> str:
        return ("reactive:" + ":".join(message_ids)
                if message_ids else f"reactive:{time.time_ns()}")

    def _respond(self, psid: str, text: str, *, deep_request: bool = False,
                 message_ids: tuple[str, ...] = ()) -> bool:
        absorbed: dict = {}
        try:
            return self._respond_turn(psid, text, deep_request=deep_request,
                                      message_ids=message_ids, absorbed=absorbed)
        finally:
            if absorbed.get("job"):
                self._settle_absorbed_debrief(absorbed)

    def _absorb_debrief(self, psid: str, absorbed: dict) -> str:
        """Un débrief Strava en attente part avec la réponse au message de l'athlète.

        Il écrit pendant l'attente : différer le débrief derrière la conversation le
        retardait de 8 min, et le faire partir à côté doublait le message. Le job est
        réclamé ici et soldé après livraison ; une capture ratée le rend à sa file.
        """
        pro = self._state.proactive
        late = ProactiveScheduler.DEBRIEF_MAX_LATE_HOURS * 3600
        for job in pro.jobs(active_only=True):
            if (job["kind"] != "strava_debrief" or job["status"] != "pending"
                    or time.time() - job["due"] > late):
                continue
            record = pro.claim_job(job["id"])
            if record is None:
                continue
            try:
                capture = self._state.ingest_strava_activity(
                    int(record["detail"]), fresh_words=True)
                snapshot = capture.get("snapshot")
                if not isinstance(snapshot, dict) or not snapshot.get("activity"):
                    raise ValueError("instantané Strava vide après capture")
            except Exception as exc:
                log.warning("Strava activity %s not folded into the reply (%s): %s",
                            record["detail"], type(exc).__name__, exc)
                pro.release_job(record["id"], delay=0)
                return ""
            absorbed["job"] = record
            absorbed["workout_card"] = snapshot.get("quality_card")
            absorbed["quality"] = bool(snapshot.get("quality"))
            log.info("Strava activity %s: debrief folded into the reply", record["detail"])
            return (strava_snapshot_note(snapshot) + "\n\n" + debrief_in_reply(
                record["detail"], carnet=self._notebook_connected(psid)))
        return ""

    def _settle_absorbed_debrief(self, absorbed: dict) -> None:
        record = absorbed["job"]
        try:
            if absorbed.get("spoken"):
                self._state.proactive.complete_job(record["id"])
                self._check_activity_recorded(ProactiveJob(
                    psid="", kind=record["kind"], prompt="", job_id=record["id"],
                    detail=record["detail"]))
            else:
                # La réponse n'est pas partie : le débrief retrouve sa file, dû.
                self._state.proactive.release_job(record["id"], delay=0)
        except Exception:
            log.exception("Débrief absorbé %s non soldé", record["id"])

    def _respond_turn(self, psid: str, text: str, *, deep_request: bool,
                      message_ids: tuple[str, ...], absorbed: dict) -> bool:
        started = time.monotonic()
        self._client.action(psid, "mark_seen")
        source_key = self._reactive_key(message_ids)
        resumed = self._resume_reactive(psid, source_key)
        if resumed is not None:
            return resumed
        outbox_before = self._outbox.snapshot()
        strength_report = any(
            message_id.startswith("strength:") for message_id in message_ids)
        # Le signe de vie part AVANT le triage : l'athlète ne regarde jamais un « Vu »
        # muet pendant que le classifieur réfléchit.
        typing = TypingIndicator(
            self._client, psid, deep=deep_request,
            gate=lambda: self._state.claim_patience(self.PATIENCE_MIN_GAP),
            deliver=lambda line: self._deliver_holding(psid, line))
        with typing:
            watch = events.Stopwatch()
            triage = self._triage(psid, text, message_ids, strength_report=strength_report)
            if deep_request:
                # /deep fixes the epistemic rung; the cheap triage still earns its keep
                # for emotion and the programme flags. `replace` and not a positional
                # rebuild: a new triage field would otherwise be lost here in silence.
                triage = replace(triage, depth="N3")
            # An emotional message keeps its rung (it may well be an N2) but loses the
            # fast announcement: "hang on, let me check your data" is the wrong answer
            # to someone saying he is discouraged.
            deep = ((deep_request or triage.depth in self.PATIENT_DEPTHS)
                    and not triage.emotional)
            typing.configure(deep=deep, holding=triage.holding)
            events.emit("turn.stage", stage="triage", kind="reactive",
                        inbound_ids=list(message_ids), depth=triage.depth,
                        plan=triage.plan_scope, duration_ms=watch.ms)
            vault_changed = self._take_vault_moved()
            self._discard_residue()
            week = self._iso_week(clock.now())
            # « P » et « D » font relire le programme et valider la semaine après le
            # tour. Ils n'ouvrent aucune porte sur la prose : seul le bloc de séances
            # dépend d'un programme valide, et celui-ci se décide sur l'état relu.
            plan_required = triage.plan_change or triage.plan_scope != "none"
            # Chargé À TOUS LES NIVEAUX, y compris N0 : `show` coûte 0,13 s, et un
            # contrôle daté est une obligation de la journée, pas un détail de lecture.
            plan_context = self._repo.weekly_plan_context(week)
            # CE QU'IL RESTAIT À FAIRE avant ce tour : c'est cette liste, et non le
            # numéro de révision, qui dit plus bas si la prescription a bougé.
            prescription_before = prescription_state(
                plan_context, clock.today().isoformat())
            handoff = self._state.handoff(psid)
            notes = self._reading_notes(
                triage, week, plan_context, plan_required=plan_required, handoff=handoff,
                psid=psid)
            packet = self._repo.coaching_context(triage.depth)
            # Le paquet N2/N3 porte déjà les fils ouverts : les rendre deux fois faisait
            # concurrence au fil réel de l'échange.
            memory = self._state.relationship_context(
                psid, threads=triage.depth not in PACKET_THREAD_DEPTHS)
            reply = self._state.reply_context(message_ids)
            timing = self._timing_note(message_ids)
            # Après le triage, sous l'indicateur de saisie : la capture relit Strava.
            debrief = "" if strength_report else self._absorb_debrief(psid, absorbed)
            corrections = self._state.correction_context(text)
            # Du plus général au plus proche du message. La relation explicite de
            # Messenger vient après la mémoire générale : plus précise, elle reste la
            # note la plus proche du message. La note du rapport renfo aussi : le modèle
            # ne doit pas confondre son éventuel refus d'outil avec le statut
            # transactionnel de la route.
            prompt = "\n\n".join(part for part in (
                corrections, memory, packet if isinstance(packet, str) else "",
                notes, reply, STRENGTH_REPORT_NOTE if strength_report else "", debrief,
                timing, text) if part)
            # Un débrief de qualité absorbé garde le rang d'un jugement (N2).
            depth = ("N2" if absorbed.get("quality") and triage.depth in {"N0", "N1"}
                     else triage.depth)
            run_plan = self._config.routing.for_depth(depth, deep_request=deep_request)
            watch = events.Stopwatch()
            outcome = self._turns.execute(
                psid,
                self._with_freshness(psid, prompt, vault_changed),
                required_weeks=(week,) if plan_required else (),
                plan=run_plan,
            )
        self._publish_due = True
        result = outcome.result
        used_spec = outcome.spec or run_plan.primary
        log.info("Reply in %.0f s (%s%s, %s, effort %s, timeout %d s, session %s)",
                 time.monotonic() - started, triage.depth,
                 " emotional" if triage.emotional else "",
                 used_spec.model or "défaut", used_spec.effort or "défaut",
                 used_spec.timeout, result.session_id or "?")
        events.emit("turn.stage", stage="model", kind="reactive", turn_id=outcome.turn_id,
                    inbound_ids=list(message_ids), route=run_plan.route.value,
                    model=used_spec.model, effort=used_spec.effort,
                    duration_ms=watch.ms, model_calls=outcome.model_calls,
                    outcome="error" if result.error else "ok",
                    failure=result.failure.value if result.failure else None)

        if result.error:
            self._log_incident(
                "agent", f"La réponse n'a pas pu être produite ({result.failure}) : "
                         f"{result.error}")
            self._discard_outbox(outbox_before)
            if not result.terminal_failure:
                self._retry_not_before = result.retry_at
                return False
            if result.failure is FailureKind.CANCELLED:
                # /cancel a déjà répondu ; le travail n'est pas relancé.
                return True
            # Le travail a déjà consommé un tour : l'entrant est acquitté pour
            # interdire une relance silencieuse du même travail.
            return self._deliver_limit(
                psid, "failure", message_ids,
                FAILURE_REPLIES.get(result.failure or FailureKind.IN_FLIGHT,
                                    FAILURE_REPLIES[FailureKind.IN_FLIGHT]))

        persisted = outcome.persistence
        if persisted is None:
            raise RuntimeError("résultat agent réussi sans résultat de persistance")
        events.emit("turn.stage", stage="apply", kind="reactive", turn_id=outcome.turn_id,
                    outcome="applied" if persisted.writes_applied else "refused",
                    commands_applied=persisted.applied_commands,
                    command_kinds=[receipt["kind"] for receipt in persisted.receipts],
                    restored=list(persisted.restored) or None)
        if persisted.restored:
            self._log_vault_alerts()

        answer = result.full_text.strip()
        if not answer:
            self._log_incident("reponse-vide", "Le tour n'a produit aucun texte, même "
                                               "après une relance dans la même session.")
            answer = EMPTY_REPLY_APPLIED if persisted.applied_commands else EMPTY_REPLY
        lines, canonical, plan_meta = self._reactive_additions(
            psid, week, triage, persisted, plan_context, prescription_before, text)

        if deep_request and (persisted.applied_commands
                             or self._repo.head() != outcome.base_head):
            # The original thread predates facts just written by its heavy fork. A
            # fresh ordinary session plus the handoff below preserves both vault
            # freshness and conversational continuity.
            self._state.clear_session(psid)

        builder = EnvelopeBuilder(psid, source_key)
        self._log_reply_shape("reactive", answer, canonical, depth=triage.depth,
                              lines=lines, turn_id=outcome.turn_id)
        builder.text(answer)
        for line in lines:
            builder.text(line, burst=False, pause_before=True)
        if canonical:
            builder.text(canonical, burst=False, pause_before=True)
        if absorbed.get("job"):
            self._render_workout_card(absorbed.get("workout_card"),
                                      str(absorbed["job"].get("detail") or ""))
        builder.images(self._outbox, outbox_before)
        builder.meta(
            kind="reactive", turn_id=outcome.turn_id,
            memory="\n---\n".join(part for part in (answer, *lines, canonical) if part),
            plan=plan_meta,
            handoff=(f"QUESTION /deep : {text}\nRÉPONSE : {result.full_text}"
                     if deep_request else ""),
            clear_handoff=bool(handoff) and not deep_request)
        envelope = builder.build()
        delivered = self._finish_reactive(
            psid, envelope.source_key, dict(envelope.meta), self._delivery.deliver(envelope))
        absorbed["spoken"] = delivered
        return delivered

    def _reactive_additions(self, psid: str, week: str, triage: Triage,
                            persisted: PersistenceResult, before_context: str,
                            prescription_before: list, text: str,
                            ) -> tuple[list[str], str, dict | None]:
        """Ce que le service AJOUTE à la prose, sans jamais la remplacer.

        Rend (lignes de limite, bloc canonique, révision montrée). Une précaution de
        sécurité écrite par le coach part toujours : aucune porte ne porte sur la prose.
        """
        lines: list[str] = []
        if not persisted.writes_applied:
            failure = persisted.failure.name if persisted.failure else "inconnue"
            self._log_incident(
                f"ecriture-refusee:{week}",
                f"{failure} : faits du tour non enregistrés. {persisted.detail}")
            self._log_vault_alerts()
            line = unrecorded_line(persisted.refused)
            if line:
                lines.append(line)
        after = self._repo.weekly_plan_context(week)
        revision_after = self._revision_of(after)
        if triage.plan_change and revision_after == self._revision_of(before_context):
            # Pas une faute en soi : « j'ai mal dormi » déclenche P et ne change souvent
            # rien. Mais c'est la signature exacte du 25/08 à 21h09, et elle n'était
            # visible nulle part. Elle l'est maintenant, avec le message qui l'a produite.
            log.warning("Tour marqué plan_change terminé SANS nouvelle révision de %s "
                        "(r%s inchangée). Message : %s", week, revision_after or "?",
                        text[:200])
        if not after:
            if triage.plan_change or triage.plan_scope != "none":
                self._log_incident(
                    f"programme-absent:{week}",
                    "Réponse envoyée sans séance : le programme de la semaine est absent "
                    "ou invalide. La réparation dédiée est armée par le planificateur.")
                events.emit("turn.stage", stage="plan_lock", kind="reactive",
                            outcome="held_prose_kept", plan=triage.plan_scope)
            if triage.plan_scope != "none":
                lines.append(PLAN_UNAVAILABLE_LINE)
            return lines, "", None
        if not persisted.writes_applied:
            # Un tour dont les faits n'ont pas été enregistrés ne prescrit rien.
            return lines, "", None
        prescription_after = prescription_state(after, clock.today().isoformat())
        try:
            canonical = self._canonical_block(
                psid, week, persisted, before_context, after,
                prescription_before, prescription_after, revision_after,
                scope=triage.plan_scope)
        except Exception:
            log.exception("Bloc canonique de %s impossible à préparer", week)
            canonical = None
        if canonical is None:
            # Programme valide mais rendu en échec : la prose part seule, la dette de
            # livraison reste posée pour le tour suivant.
            try:
                self._state.owe_plan(psid, week, revision_after)
            except Exception:
                log.exception("Dette de livraison du programme non posée")
            return [], "", None
        return [], canonical, ({"week": week, "revision": revision_after}
                               if canonical else None)

    def _resume_reactive(self, psid: str, source_key: str) -> bool | None:
        """Renvoie une réponse déjà calculée pour ces messages, sans nouveau tour."""
        stored = self._delivery.stored(source_key)
        if stored is None:
            return None
        if stored.status in {"prepared", "failed"} and not stored.resendable:
            # Trace sans contenu (antérieure au stockage des enveloppes) : on recalcule.
            self._delivery.discard(source_key)
            return None
        log.info("Réponse déjà calculée pour %s : reprise sans nouveau tour (%s)",
                 source_key, stored.status)
        return self._finish_reactive(psid, source_key, stored.meta,
                                     self._delivery.resume(stored))

    def _finish_reactive(self, psid: str, source_key: str, meta: dict,
                         delivery: DeliveryReceipt) -> bool:
        """Acquitte ce qui a franchi Meta ; la mémoire ne peut plus faire rejouer le tour."""
        self._report_uncertain_delivery(source_key, delivery)
        if not delivery.text_ok and not delivery.delivered_any:
            log.error("Réponse non livrée ; elle sera renvoyée telle quelle (%s)",
                      delivery.detail or "cause inconnue")
            events.emit("turn.stage", stage="deliver", kind=meta.get("kind"),
                        turn_id=meta.get("turn_id") or None, outcome="failed",
                        delivery="failed")
            return False
        memory = (str(meta.get("memory") or "") if delivery.text_ok else "\n".join(
            item.text for item in delivery.messages if item.text))
        try:
            if memory.strip():
                self._state.record_outbound(
                    psid, memory, source_key=source_key,
                    turn_id=str(meta.get("turn_id") or ""),
                    platform_messages=self._platform_messages(delivery))
            plan = meta.get("plan")
            if plan and delivery.text_ok:
                self._state.settle_plan(psid)
                self._state.proactive.mark(self._plan_shown_marker(
                    plan["week"], int(plan["revision"]), clock.today().isoformat()))
            if meta.get("handoff"):
                self._state.set_handoff(psid, str(meta["handoff"]))
            elif meta.get("clear_handoff"):
                # La session ordinaire a maintenant lu la passerelle : la garder ferait
                # payer et répéter le même contexte à chaque tour.
                self._state.clear_handoff(psid)
        except Exception:
            log.exception("Écritures d'après livraison impossibles")
            self._log_incident(
                f"memoire-apres-livraison:{source_key}",
                "La réponse est partie, mais sa trace mémoire n'a pas pu être enregistrée.")
        if not delivery.text_ok:
            # Messenger n'offre aucune idempotence réseau : un fragment accepté suffit
            # à acquitter, comme pour les proactifs partiellement livrés.
            self._log_incident(f"reponse-partielle:{source_key}",
                               "La réponse n'a été livrée qu'en partie.")
        if not delivery.attachments_ok:
            self._log_incident(
                "piece-jointe-reactive",
                "La réponse est partie, mais sa pièce jointe n'a pas pu être envoyée.")
        self._last_reactive_reply = time.time()
        events.emit("turn.stage", stage="deliver", kind=meta.get("kind"),
                    turn_id=meta.get("turn_id") or None,
                    outcome="delivered" if delivery.text_ok else "partial",
                    delivery="replayed" if delivery.replayed else "sent",
                    fragments=delivery.delivered)
        self._log_vault_alerts()
        return True

    def _deliver_limit(self, psid: str, prefix: str, message_ids: tuple[str, ...],
                       text: str) -> bool:
        """Une phrase de limite, livrée et mémorisée ; l'entrant est acquitté quoi qu'il
        arrive, pour ne jamais relancer un travail qui a déjà échoué en route."""
        source_key = (f"{prefix}:" + ":".join(message_ids)
                      if message_ids else f"{prefix}:{time.time_ns()}")
        try:
            delivery = self._delivery.deliver(
                EnvelopeBuilder(psid, source_key).text(text, burst=False).build())
            self._report_uncertain_delivery(source_key, delivery)
            if delivery.text_ok or delivery.delivered_any:
                self._state.record_outbound(
                    psid, text, source_key=source_key, resolve_questions=False,
                    platform_messages=self._platform_messages(delivery))
            else:
                self._log_incident(f"limite-non-livree:{source_key}", delivery.detail)
        except Exception:
            log.exception("Phrase de limite impossible à livrer")
        return True

    def _deliver_holding(self, psid: str, line: str) -> None:
        """La phrase d'attente est un vrai message : livrée et mémorisée comme les autres."""
        source_key = f"holding:{uuid.uuid4()}"
        delivery = self._delivery.deliver(
            EnvelopeBuilder(psid, source_key).text(line, burst=False).build())
        if delivery.text_ok:
            self._state.record_outbound(
                psid, line, source_key=source_key, resolve_questions=False,
                platform_messages=self._platform_messages(delivery))

    @staticmethod
    def _revision_of(context: str) -> int:
        """Le numéro de la révision active, 0 si le plan est absent ou illisible."""
        try:
            return int(json.loads(context).get("revision", 0))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            return 0

    def _plan_delivery_motive(self, psid: str, week: str, before: list, after: list,
                              revision: int, scope: str) -> str:
        """Return the observed reason to send the canonical plan, if any."""
        if not revision:
            return ""
        if after != before:
            return "la prescription a changé"
        if scope != "none":
            return "demandé par ⟨athlète⟩"
        if self._state.plan_owed(psid) == (week, revision):
            return "livraison précédente perdue"
        return ""

    @staticmethod
    def _plan_shown_marker(week: str, revision: int, day: str) -> str:
        return f"plan-shown:{week}:r{revision}:{day}"

    def _canonical_block(self, psid: str, week: str, persisted: PersistenceResult,
                         before_context: str, context: str,
                         remaining_before: list, remaining_after: list,
                         revision: int, *, scope: str) -> str | None:
        """Render a deliverable plan, ``""`` when held, or ``None`` on render failure."""
        if not persisted.writes_applied:
            log.info("Semaine %s : écritures refusées, aucune prescription ne part", week)
            return ""
        motive = self._plan_delivery_motive(
            psid, week, remaining_before, remaining_after, revision, scope)
        if not motive:
            if revision:
                log.info("Programme %s r%s inchangé et déjà reçu : le coach répond "
                         "dessus sans le répéter", week, revision)
            return ""
        # Le statut est relu au moment d'envoyer, que le triage ait ou non demandé la
        # validation de la semaine : un contrôle daté ou une péremption retient les
        # séances, quel que soit le message qui a déclenché l'envoi.
        if week in persisted.awaiting:
            status = PlanStatus.AWAITING
        elif week in persisted.stale:
            status = PlanStatus.STALE
        else:
            status = self._repo.validate_weekly_plan(week)
        excluded: frozenset[str] = frozenset()
        if status is PlanStatus.AWAITING:
            excluded = blocked_session_ids(context, clock.today().isoformat())
            log.info("Semaine %s : séances retenues par contrôle daté : %s",
                     week, ", ".join(sorted(excluded)))
            if not prescription_lines(
                    context, clock.today().isoformat(), exclude_ids=excluded):
                log.info("Semaine %s : toutes les séances restantes sont retenues", week)
                return ""
        if status is PlanStatus.STALE:
            # Même traitement qu'un contrôle daté, autre remède : le programme ne
            # décrit plus la semaine, il attend une révision. Le tour suivant la
            # réclamera au coach, en tête de son prompt.
            log.info("Semaine %s périmée : le coach parle, la prescription attend "
                     "une révision", week)
            return ""
        log.info("Programme %s r%s envoyé : %s", week, revision, motive)
        if scope != "none":
            canonical = render_plan_for_scope(
                context, week, scope, today=clock.today().isoformat(),
                exclude_ids=excluded)
        elif motive == "la prescription a changé":
            canonical = render_plan_delta(
                before_context, context, week, today=clock.today().isoformat(),
                exclude_ids=excluded)
        else:
            # Une dette de livraison ne reconstruit pas le delta perdu : elle renvoie
            # l'état restant, seule vérité encore disponible.
            canonical = render_plan_for_scope(
                context, week, "remaining", today=clock.today().isoformat(),
                exclude_ids=excluded)
        if not canonical:
            self._log_incident(
                f"rendu-plan:{week}",
                f"Programme {week} enregistré et valide, mais son rendu a échoué. "
                "La prose part seule ; le programme reste dû.")
            return None
        # La dette se pose AVANT le transport : un texto perdu doit rester dû.
        self._state.owe_plan(psid, week, revision)
        return canonical

    def _log_vault_alerts(self) -> None:
        """Vide les alertes de persistance dans les journaux d'exploitation."""
        for alert in self._repo.drain_alerts():
            self._log_incident(f"vault:{alert.key}", alert.text)

    @staticmethod
    def _log_incident(key: str, text: str) -> None:
        """Journalise un incident interne sans le transformer en état métier."""
        log.error("Incident interne « %s » : %s", key, text)

    @staticmethod
    def _log_reply_shape(kind: str, prose: str, canonical: str, *, depth: str = "",
                         lines: Sequence[str] = (), turn_id: str | None = None) -> None:
        """Mesure passive : aucune limite, aucune réécriture, aucune nouvelle inférence.

        Ce que l'athlète reçoit est l'enveloppe, pas la prose du modèle : les bulles se
        comptent avec le découpage du transport, et ce que le service ajoute (limites,
        bloc de programme) se mesure à part. Des compteurs seulement, jamais le texte.
        """
        delivered = strip_links(prose).strip()
        added = [part for part in (*lines, canonical) if part.strip()]
        events.emit("turn.stage", stage="shape", kind=kind, depth=depth or None, turn_id=turn_id or None,
                    prose_chars=len(delivered),
                    prose_bubbles=len(burst_parts(delivered)) if delivered else 0,
                    added_chars=sum(len(part) for part in added), added_parts=len(added),
                    questions=delivered.count("?"))

    def _discard_residue(self) -> None:
        """Nettoyer avant de travailler. Sans message : un résidu jeté dont le coach se
        relève tout seul est un non-événement pour l'athlète, et il est déjà au journal."""
        discarded = self._repo.ensure_clean()
        if discarded:
            log.error("Tour précédent mort sans persister ; %d fichier(s) abandonné(s)",
                      len(discarded))

    @staticmethod
    def _iso_week(moment: datetime, offset_days: int = 0) -> str:
        """La semaine ISO, calculée par le modèle qui la déclare partout ailleurs."""
        return weekly_plan_model.current_week(
            moment.date() + timedelta(days=offset_days))

    def _logical_moment(self, job: ProactiveJob, observed: datetime) -> datetime:
        """Anchor a periodic retry to the period encoded by its durable key.

        `weekly:2026-W34` désigne la clôture de W34, même si le budget ne laisse le
        job repartir que le lundi de W35. Recalculer depuis l'heure du retry faisait
        demander W36, puis déclarer le plan incohérent alors que W35 était valide.
        Les jobs ad hoc et les anciennes clés non conformes gardent l'heure observée.
        """
        prefix = "weekly:"
        if job.kind != "weekly" or not job.job_id.startswith(prefix):
            return observed
        value = job.job_id.removeprefix(prefix)
        try:
            year_text, week_text = value.split("-W", 1)
            sunday = datetime.fromisocalendar(int(year_text), int(week_text), 7).date()
        except (ValueError, TypeError):
            log.error("Clé de bilan hebdomadaire illisible : %s", job.job_id)
            return observed
        if observed.date() == sunday:
            # Parti dès que le dimanche est soldé : l'heure réelle, pas celle du repli.
            return observed
        scheduled = self._config.proactive_weekly or observed.timetz().replace(tzinfo=None)
        return clock.combine(sunday, scheduled)

    # ---- proactive

    def _proactive_key(self, job: ProactiveJob) -> str:
        return (f"proactive:{job.job_id}" if job.job_id
                else f"proactive:{job.kind}:{time.time_ns()}")

    def _defer_active_proactive(self, job: ProactiveJob, *,
                                envelope: OutboundEnvelope | None = None) -> bool:
        """Le réactif prime, sans détruire : une sortie déjà calculée est conservée."""
        if not self.conversation_active(job.psid):
            return False
        log.info("Proactive %s delayed: athlete is active", job.kind)
        if envelope is not None:
            self._delivery.prepare(envelope)
        self._release_job(job)
        return True

    def _respond_proactive(self, job: ProactiveJob) -> None:
        # Éteindre aussi les anciennes relances déjà en file, sans réveiller l'agent.
        if job.kind == "gear_alert":
            try:
                detail = json.loads(job.detail)
            except (TypeError, ValueError):
                detail = {}
            if isinstance(detail, dict) and detail.get("reason") == "rotation":
                self._complete_job(job)
                return
        if self._defer_active_proactive(job):
            return
        if self._resume_proactive(job):
            return
        if job.kind == "strength_card":
            self._deliver_strength_card_job(job)
            return
        if job.kind == "registre_incomplet":
            self._deliver_missing_cells(job)
            return
        if job.kind == "trace_reveil" and not self.wake_reminder_due(job.psid):
            # Donnée dans le carnet (ou dans le chat) entre la mise en file et l'envoi.
            self._complete_job(job)
            return
        if job.kind in CLOSED_KINDS:
            closed = compose_closed_proactive(job.kind, job.detail)
            if closed is None:
                self._fail_job(job, "détail illisible pour un message fermé")
                return
            self._deliver_closed_proactive(job, closed)
            return
        if not job.prompt:
            self._fail_job(job, f"aucun prompt pour le type {job.kind}")
            return
        self._run_proactive_agent(job)

    def _run_proactive_agent(self, job: ProactiveJob) -> None:
        psid = job.psid
        started = time.monotonic()
        outbox_before = self._outbox.snapshot()
        heavy = job.kind in self.HEAVY_KINDS
        # Un travail lourd part d'une session vierge. Un proactif léger part d'une COPIE
        # de la conversation : non livré, il ne laisse aucune trace dans la session
        # principale, qu'il n'y a donc jamais à effacer.
        mode = SessionMode.ISOLATED if heavy else SessionMode.FORK
        # Un débrief de qualité juge la séance (critères, calibration, suite) : le
        # 15/10, un N1 à effort bas y a inventé un critère de FC et sauté la carte.
        route = Route.HEAVY if heavy else (Route.N2 if job.quality else Route.N1)
        run_plan = (self._config.routing.plan(route)
                    .with_session_mode(mode).with_structured_reply())
        log.info("Proactive %s running on %s (effort %s, timeout %d s, %s, tentative %d)",
                 job.kind, run_plan.primary.model or "défaut",
                 run_plan.primary.effort or "défaut", run_plan.primary.timeout,
                 mode.value, job.attempts)
        # Aucun indicateur de saisie : l'athlète n'attend rien, et « … » pendant quarante
        # minutes pour finir silencieux l'incitait seulement à écrire.
        vault_changed = self._take_vault_moved()
        self._discard_residue()
        prompt = job.prompt if heavy else self._with_freshness(
            psid, job.prompt, vault_changed)
        memory = self._state.relationship_context(psid)
        if memory:
            prompt = memory + "\n\n" + prompt
        now_for_plan = self._logical_moment(job, clock.now())
        prompt, strength_session, required_weeks = self._proactive_prompt(
            job, prompt, now_for_plan)
        watch = events.Stopwatch()
        outcome = self._turns.execute(
            psid, prompt, required_weeks=required_weeks, plan=run_plan)
        self._publish_due = True
        result = outcome.result
        used_spec = outcome.spec or run_plan.primary
        events.emit("turn.stage", stage="model", kind=job.kind, job_id=job.job_id,
                    turn_id=outcome.turn_id, route=run_plan.route.value,
                    model=used_spec.model, effort=used_spec.effort,
                    duration_ms=watch.ms, model_calls=outcome.model_calls,
                    outcome="error" if result.error else ("silent" if result.silent else "ok"),
                    failure=result.failure.value if result.failure else None,
                    attempt=job.attempts)
        if result.error:
            self._discard_outbox(outbox_before)
            if result.terminal_failure:
                self._fail_job(job, f"{result.failure.value if result.failure else 'panne'}"
                                    f" : {result.error}")
            else:
                self._buffer_proactive_retry(
                    job, retry_at=result.retry_at, cause=f"moteur indisponible : {result.error}")
            return
        persisted = outcome.persistence
        if persisted is None:
            raise RuntimeError("résultat agent réussi sans résultat de persistance")
        if not persisted.writes_applied:
            # Une INITIATIVE qui échoue ne prend jamais la parole du coach : c'est le
            # service qui n'a pas pu faire son travail, pas le coach qui a quelque chose
            # à dire. L'incident reste au journal d'exploitation.
            self._discard_outbox(outbox_before)
            self._log_vault_alerts()
            self._buffer_proactive_retry(
                job, cause=f"écriture refusée : {persisted.detail}")
            return
        canonical, retry_cause = self._proactive_canonical(job, persisted, now_for_plan)
        if retry_cause:
            self._discard_outbox(outbox_before)
            self._log_vault_alerts()
            self._buffer_proactive_retry(job, cause=retry_cause)
            return
        strength_card = self._strength_card_after(job, persisted, strength_session,
                                                  now_for_plan)
        reply = "" if result.silent else result.full_text.strip()
        if not reply and not canonical:
            self._discard_outbox(outbox_before)
            self._log_vault_alerts()
            if job.kind in SILENCE_IS_A_FAILURE:
                # Un débrief n'existe QUE parce qu'une activité vient d'arriver : son
                # silence veut dire que la sortie n'a pas été lue. Une seule reprise.
                cause = "débrief automatique muet"
                if job.attempts >= 2:
                    self._fail_job(job, cause)
                else:
                    self._buffer_proactive_retry(job, cause=cause)
                return
            if job.kind in MUST_SPEAK_KINDS:
                self._fail_job(job, "travail terminé sans message")
                return
            log.info("Proactive %s -> silence (nothing useful to say)", job.kind)
            self._complete_job(job)
            return
        if not reply and job.kind in MUST_SPEAK_KINDS:
            self._log_incident(f"{job.kind}-sans-texte:{job.job_id}",
                               "Le programme part sans le bilan qui devait l'accompagner.")
        message = "\n---\n".join(part for part in (reply, canonical) if part)
        self._log_reply_shape(job.kind, reply, canonical, turn_id=outcome.turn_id)
        builder = self._proactive_builder(job)
        workout_card = self._render_workout_card(job.workout_card, job.detail)
        builder.text(message).images(self._outbox, outbox_before)
        revision_check = self._revision_check(job, now_for_plan) if canonical else None
        builder.meta(kind=job.kind, turn_id=outcome.turn_id, memory=message,
                     strength_card=strength_card, revision_check=revision_check,
                     plan_card=(self._iso_week(now_for_plan, offset_days=7)
                                if job.kind == "weekly" and canonical else None),
                     workout_card=workout_card,
                     check_activity=job.kind in SILENCE_IS_A_FAILURE)
        self._send_proactive(job, builder.build())
        log.info("Proactive %s sent in %.0f s", job.kind, time.monotonic() - started)

    def _render_workout_card(self, card: dict | None, activity_id: str) -> dict | None:
        """Rend la carte des répétitions dans l'outbox ; rend la charge utile si elle part.

        Déterministe, comme la carte de renfo : le 15/10, le premier fractionné avec
        Redline est parti sans carte parce qu'elle était laissée au choix du modèle.
        """
        if not isinstance(card, dict) or not card.get("reps"):
            return None
        if not self._repo.render_workout_card(card, activity_id or "seance"):
            self._log_incident(f"carte-seance:{activity_id}",
                               "Le débrief d'une séance de qualité part sans sa carte.")
            return None
        return card

    def _proactive_builder(self, job: ProactiveJob) -> EnvelopeBuilder:
        messaging_type, tag = ("UPDATE", None)
        if not self._state.within_24h(job.psid):
            messaging_type, tag = ("MESSAGE_TAG", "ACCOUNT_UPDATE")
        return EnvelopeBuilder(job.psid, self._proactive_key(job), messaging_type, tag)

    def _revision_check(self, job: ProactiveJob, moment: datetime) -> dict | None:
        """La révision que le texte préparé montre : une reprise doit la retrouver."""
        week = {"weekly": self._iso_week(moment, offset_days=7),
                "weekly_repair": job.detail or self._iso_week(moment)}.get(
                    job.kind, self._iso_week(moment))
        revision = self._revision_of(self._repo.weekly_plan_context(week))
        return {"week": week, "revision": revision} if revision else None

    def _proactive_canonical(self, job: ProactiveJob, persisted: PersistenceResult,
                             moment: datetime) -> tuple[str, str]:
        """(bloc de séances, cause de reprise). Une reprise ne vient que d'un devoir
        manqué (bilan sans semaine suivante) ; un programme absent n'est jamais une
        raison de régénérer un mot du matin."""
        today = moment.date().isoformat()
        if job.kind == "morning":
            week = self._iso_week(moment)
            context = self._repo.weekly_plan_context(week)
            if week in persisted.invalid or not context:
                log.info("Mot du matin sans séance : le programme de %s est absent ou "
                         "invalide, sa réparation dédiée est armée", week)
                self._arm_week_repair(week, moment)
                return "", ""
            revision = self._revision_of(context)
            already_shown = revision and self._state.proactive.marked(
                self._plan_shown_marker(week, revision, today))
            if already_shown:
                log.info("Mot du matin : programme %s r%s déjà montré aujourd'hui",
                         week, revision)
                return "", ""
            if week in persisted.awaiting:
                blocked = blocked_session_ids(context, today)
                log.info("Mot du matin : contrôle daté de %s, séances retenues : %s",
                         week, ", ".join(sorted(blocked)))
                return render_plan_message(
                    context, week, today=today, exclude_ids=blocked), ""
            if week in persisted.stale:
                # Le mot du matin part quand même — il prend des nouvelles, relance un
                # fil, pose la question du jour. Seule la ligne de séances attend que le
                # programme soit remis en face du réalisé.
                log.info("Mot du matin sans séance : le programme de %s est périmé et "
                         "attend une révision", week)
                return "", ""
            canonical = self._repo.weekly_plan_message(week, today=today)
            if not canonical:
                self._log_incident(
                    f"rendu-plan:{week}",
                    f"Programme {week} valide mais non rendu : le mot du matin part sans "
                    "ses séances.")
            return canonical, ""
        if job.kind == "weekly":
            week = self._iso_week(moment, offset_days=7)
            if week in persisted.invalid:
                return "", f"semaine {week} toujours absente ou invalide après le bilan"
            canonical = self._repo.weekly_plan_message(week)
            if not canonical:
                self._log_incident(
                    f"rendu-plan:{week}",
                    f"Programme {week} valide mais non rendu : le bilan part sans lui.")
                return "", ""
            self._repo.render_weekly_plan_card(week)
            return canonical, ""
        if job.kind == "weekly_repair":
            week = job.detail or self._iso_week(moment)
            if week in persisted.invalid:
                return "", f"semaine {week} toujours absente ou invalide après la réparation"
            canonical = self._repo.weekly_plan_message(week, today=today)
            if not canonical:
                self._log_incident(
                    f"rendu-plan:{week}",
                    f"Programme {week} réparé mais non rendu : le message part sans lui.")
            return canonical, ""
        return "", ""

    def _strength_card_after(self, job: ProactiveJob, persisted: PersistenceResult,
                             strength_session: dict | None,
                             moment: datetime) -> dict | None:
        """La carte de renfo du jour, armée seulement si la prescription finale la porte."""
        if job.kind != "morning" or not strength_session:
            return None
        current_week = self._iso_week(moment)
        refreshed = self._repo.weekly_plan_context(current_week)
        session = strength_due(refreshed, moment.date().isoformat())
        blocked = blocked_session_ids(refreshed, moment.date().isoformat())
        held = (current_week in persisted.stale
                or (session and session.get("id") in blocked))
        if not session or session.get("strength") is None or held:
            return None
        return {"week": current_week, "revision": self._revision_of(refreshed),
                "session_id": session["id"], "date": moment.date().isoformat()}

    def _send_proactive(self, job: ProactiveJob, envelope: OutboundEnvelope) -> None:
        """Envoie une initiative prête, ou la garde telle quelle pour plus tard."""
        if self._defer_active_proactive(job, envelope=envelope):
            return
        now = clock.now()
        if not self._state.proactive.would_pass(now, self._config, ignore_gap=job.priority):
            log.info("Proactive %s prête mais hors plage ou trop rapprochée : conservée",
                     job.kind)
            self._delivery.prepare(envelope)
            self._release_job(job)
            return
        self._finish_proactive(job, dict(envelope.meta), self._delivery.deliver(envelope), now)

    def _resume_proactive(self, job: ProactiveJob) -> bool:
        """Livre une initiative déjà rédigée au lieu de relancer son tour de modèle."""
        if not job.job_id or job.kind not in PROACTIVE_TRIGGERS:
            # Les rendus fermés se recalculent gratuitement et à l'identique.
            return False
        source_key = self._proactive_key(job)
        stored = self._delivery.stored(source_key)
        if stored is None:
            return False
        if stored.status in {"delivered", "sending"}:
            self._finish_proactive(job, stored.meta, self._delivery.resume(stored),
                                   clock.now())
            return True
        if not stored.resendable:
            self._delivery.discard(source_key)
            return False
        check = stored.meta.get("revision_check")
        if check and self._revision_of(
                self._repo.weekly_plan_context(check["week"])) != check["revision"]:
            # Le programme a bougé depuis la rédaction : le texte ne décrit plus la
            # semaine. Seule cette vérification déterministe décide d'une réécriture.
            log.info("Initiative préparée %s périmée : le programme a changé", job.job_id)
            self._delivery.discard(source_key)
            return False
        now = clock.now()
        if not self._state.proactive.would_pass(now, self._config, ignore_gap=job.priority):
            self._release_job(job)
            return True
        log.info("Initiative %s déjà rédigée : livraison sans nouveau tour", job.job_id)
        images: tuple[ImagePart, ...] = ()
        if stored.meta.get("plan_card") or stored.meta.get("workout_card"):
            before = self._outbox.snapshot()
            if stored.meta.get("plan_card"):
                self._repo.render_weekly_plan_card(str(stored.meta["plan_card"]))
            self._render_workout_card(stored.meta.get("workout_card"), job.detail)
            images = tuple(ImagePart(path, self._outbox.link_for(path))
                           for path in self._outbox.collect_new(before))
        self._finish_proactive(job, stored.meta,
                               self._delivery.redeliver(stored, images), now)
        return True

    def _finish_proactive(self, job: ProactiveJob, meta: dict,
                          delivery: DeliveryReceipt, now: datetime) -> None:
        source_key = self._proactive_key(job)
        self._report_uncertain_delivery(source_key, delivery)
        if not delivery.text_ok and not delivery.delivered_any:
            self._log_incident(
                f"proactif-non-livre:{job.job_id or job.kind}",
                f"L'envoi « {job.kind} » n'a pas été livré : "
                f"{delivery.detail or 'cause inconnue'}. Le texte est conservé.")
            self._buffer_proactive_retry(
                job, cause=f"livraison : {delivery.detail or 'cause inconnue'}")
            return
        text = (str(meta.get("memory") or "") if delivery.text_ok else "\n".join(
            item.text for item in delivery.messages if item.text))
        self._finish_proactive_delivery(
            job, now, text, str(meta.get("turn_id") or ""),
            platform_messages=self._platform_messages(delivery),
            questions=tuple(meta.get("questions") or ()))
        if not delivery.text_ok:
            self._log_incident(
                f"proactif-partiel:{job.job_id or job.kind}",
                f"L'envoi « {job.kind} » n'a été livré qu'en partie : "
                f"{delivery.detail or 'cause inconnue'}.")
            return
        if not delivery.attachments_ok:
            # Le texte est déjà chez l'athlète. Rejouer le job le dupliquerait ; la pièce
            # jointe refusée est en quarantaine et l'incident est explicite.
            self._log_incident(
                f"piece-jointe-proactive:{job.job_id or job.kind}",
                f"L'envoi « {job.kind} » est parti sans sa pièce jointe.")
        self._mark_cells_asked(meta.get("markers") or ())
        if meta.get("check_activity"):
            self._check_activity_recorded(job)
        if meta.get("strength_card"):
            self._schedule_strength_card(job.psid, dict(meta["strength_card"]))
        self._log_vault_alerts()

    def _buffer_proactive_retry(self, job: ProactiveJob, *, cause: str,
                                retry_at: float | None = None) -> None:
        """Reprise bornée d'une initiative : délai croissant, trois réclamations au plus.

        L'échéance est ancrée sur la première tentative (`job.expire_at`) pour ne pas
        dériver. Au-delà, le job passe `failed` avec sa cause : jamais de boucle.
        """
        if not job.job_id:
            self._log_incident(f"proactif-sans-identite:{job.kind}", cause)
            return
        if job.attempts >= self.PROACTIVE_MAX_ATTEMPTS:
            self._fail_job(job, f"{cause} ; {job.attempts} tentatives")
            return
        now = time.time()
        budget = self.RETRY_STALENESS_HOURS.get(job.kind, 4.0)
        expire = job.expire_at or now + budget * 3600
        delay = self.PROACTIVE_RETRY_DELAYS[
            min(job.attempts, len(self.PROACTIVE_RETRY_DELAYS)) - 1]
        due = max(now + delay, retry_at or 0.0)
        if due >= expire:
            self._fail_job(job, f"{cause} ; plus pertinent à la reprise")
            return
        self._state.proactive.retry_job(job.job_id, due, expire)
        events.emit("proactive.retry", job_id=job.job_id, kind=job.kind,
                    attempt=job.attempts, retry_in_s=int(due - now), cause=cause[:200])
        log.info("Proactive %s deferred (%s): new attempt around %s", job.kind, cause,
                 clock.fromtimestamp(due).strftime("%Hh%M"))

    def _fail_job(self, job: ProactiveJob, cause: str) -> None:
        """Solde un job non livré en `failed`, avec sa cause, distinct d'un succès."""
        if job.job_id:
            self._state.proactive.fail_job(job.job_id, cause[:500])
        self._log_incident(f"proactif-echec:{job.job_id or job.kind}",
                           f"« {job.kind} » abandonné : {cause}")
        events.emit("proactive.failed", job_id=job.job_id or None, kind=job.kind,
                    attempt=job.attempts, cause=cause[:200])

    def _release_job(self, job: ProactiveJob, delay: float = 60.0) -> None:
        """Remet un job réclamé en file, toujours avec une échéance."""
        if not job.job_id:
            return
        budget = self.RETRY_STALENESS_HOURS.get(job.kind, 4.0)
        expire = job.expire_at or time.time() + budget * 3600
        if self._state.proactive.release_job(job.job_id, delay, expire_at=expire):
            log.info("Proactive %s released", job.job_id)
        else:
            self._log_incident(f"proactif-echec:{job.job_id}",
                               f"« {job.kind} » expiré avant d'avoir pu partir.")

    def _complete_job(self, job: ProactiveJob) -> None:
        if job.job_id:
            self._state.proactive.complete_job(job.job_id)

    def materialize_week_repair(self, moment: datetime) -> bool:
        """Invariant du tick : la semaine courante a un programme valide, ou un
        propriétaire qui le répare. Vérifié au plus tous les quarts d'heure."""
        if moment.timestamp() - self._week_repair_checked < self.WEEK_REPAIR_CHECK_SECONDS:
            return False
        self._week_repair_checked = moment.timestamp()
        if moment.weekday() == 6:
            # Le dimanche appartient au bilan, qui prépare la semaine suivante.
            return False
        week = self._iso_week(moment)
        if self._repo.validate_weekly_plan(week) is not PlanStatus.INVALID:
            return False
        return self._arm_week_repair(week, moment)

    def _arm_week_repair(self, week: str, moment: datetime) -> bool:
        if any(job["kind"] in {"weekly", "weekly_repair"}
               for job in self._state.proactive.jobs(active_only=True)):
            return False
        year, number = week.split("-W")
        sunday = datetime.fromisocalendar(int(year), int(number), 7).date()
        expires = clock.combine(sunday, datetime.max.time().replace(microsecond=0))
        inserted = self._state.proactive.enqueue_job(
            f"weekly-repair:{week}", "weekly_repair", week, moment.timestamp(),
            expire_at=max(expires.timestamp(), moment.timestamp() + 3600), priority=True)
        if inserted:
            self._log_incident(f"programme-absent:{week}",
                               "Semaine courante sans programme valide : réparation armée.")
        return inserted

    def _schedule_strength_card(self, psid: str, detail: dict) -> None:
        """Arme une livraison idempotente puis la met immédiatement en file locale."""
        identity = (
            f"strength-card:{detail['week']}:r{detail['revision']}:"
            f"{detail['session_id']}:{detail['date']}"
        )
        encoded = json.dumps(
            detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        inserted = self._state.proactive.enqueue_job(
            identity, "strength_card", encoded, time.time(), priority=True)
        if inserted:
            self.submit_proactive(ProactiveJob(
                psid=psid, kind="strength_card", prompt="", job_id=identity,
                detail=encoded, priority=True))

    def materialize_strength_card(self, psid: str, moment: datetime) -> bool:
        """Réarme la dette du jour indépendamment du succès du mot du matin.

        Le scheduler l'appelle à chaque tick. L'identité stable rend l'opération
        gratuite après livraison et récupère un matin manqué ou un redémarrage.
        """
        today = moment.date().isoformat()
        week = self._iso_week(moment)
        context = self._repo.weekly_plan_context(week)
        session = strength_due(context, today)
        if (not session or session.get("strength") is None
                or session.get("id") in blocked_session_ids(context, today)):
            return False
        revision = self._revision_of(context)
        if not revision:
            return False
        detail = {
            "week": week, "revision": revision,
            "session_id": session["id"], "date": today,
        }
        identity = f"strength-card:{week}:r{revision}:{session['id']}:{today}"
        encoded = json.dumps(
            detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return self._state.proactive.enqueue_job(
            identity, "strength_card", encoded, moment.timestamp(), priority=True)

    def _strength_card_context(
            self, detail: dict | None = None,
    ) -> tuple[str, str, dict | None]:
        """Relit la révision active sans confondre absence et indisponibilité.

        ``weekly_plan_context`` est vide aussi bien pour une semaine absente que pour
        une semaine invalide. Ce vide ne prouve donc jamais qu'il n'y a pas de renfo.
        La commande utilisateur doit nommer la limite, tandis qu'une dette proactive
        devenue obsolète peut simplement être soldée.
        """
        today = str((detail or {}).get("date") or clock.today().isoformat())
        week = str((detail or {}).get("week") or self._iso_week(clock.now()))
        status = self._repo.validate_weekly_plan(week)
        if status is PlanStatus.INVALID:
            return "unavailable", today, None
        if status is PlanStatus.STALE:
            return "stale", today, None
        context = self._repo.weekly_plan_context(week)
        if not context:
            return "unavailable", today, None
        session = strength_due(context, today)
        if not session:
            return "none", today, None
        if session.get("id") in blocked_session_ids(context, today):
            return "blocked", today, session
        if detail:
            try:
                revision = int(json.loads(context).get("revision", 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                return "unavailable", today, None
            if (session.get("id") != detail.get("session_id")
                    or revision != int(detail.get("revision", -1))):
                return "obsolete", today, session
        return "ready", today, session

    # Ce que `/renfo` répond quand la carte ne peut pas partir, en mots de coureur.
    STRENGTH_CARD_LIMITS: ClassVar[dict[str, str]] = {
        "unavailable": (
            "Je n'ai pas de programme fiable pour aujourd'hui en ce moment, donc je ne "
            "peux pas te confirmer s'il y a du renfo. Je m'en occupe."),
        "stale": (
            "Ta semaine a bougé depuis le dernier programme : je le remets à jour avant "
            "de t'envoyer la carte de renfo."),
        "blocked": (
            "Le renfo du jour est prévu, mais j'attends d'abord ta réponse à ma question "
            "du jour avant de t'envoyer sa carte."),
        "none": "Aucune séance de renforcement n'est prescrite aujourd'hui.",
    }

    def _deliver_strength_card(self, psid: str, source_key: str, *,
                               detail: dict | None = None,
                               job: ProactiveJob | None = None) -> bool:
        """Rend et livre une carte depuis le plan, sans appel possible à l'agent."""
        state, today, session = self._strength_card_context(detail)
        if state != "ready":
            if job:
                log.info("Carte de renfo %s devenue obsolète", job.job_id)
                self._complete_job(job)
                return True
            envelope = (EnvelopeBuilder(psid, source_key)
                        .text(self.STRENGTH_CARD_LIMITS.get(
                            state, self.STRENGTH_CARD_LIMITS["unavailable"]),
                              burst=False).build())
            delivery = self._delivery.deliver(envelope)
            return delivery.text_ok or delivery.delivered_any

        assert session is not None
        try:
            target = render_strength_artifact(session, self._outbox, today,
                                              root=Path(self._config.vault_path))
        except Exception as exc:
            log.exception("Rendu déterministe de la carte de renfo impossible")
            self._log_incident(
                f"carte-renfo:{today}",
                f"La prescription {session.get('id')} est valide mais son rendu a échoué : "
                f"{exc.__class__.__name__}.")
            if job:
                self._buffer_proactive_retry(job, cause="rendu de la carte impossible")
                return False
            envelope = (EnvelopeBuilder(psid, source_key)
                        .text("La séance de renfo est bien prévue, mais je n'arrive pas à "
                              "générer sa carte pour l'instant.", burst=False).build())
            delivery = self._delivery.deliver(envelope)
            return delivery.text_ok or delivery.delivered_any

        title = str(session.get("title") or "renforcement").strip()
        if job and self._defer_active_proactive(job):
            self._outbox.archive(target)
            return False
        envelope = (EnvelopeBuilder(psid, source_key)
                    .text(f"Ta carte de renfo du jour — {title}.", burst=False)
                    .image(self._outbox, target).build())
        delivery = self._delivery.deliver(envelope)
        self._report_uncertain_delivery(source_key, delivery)
        accepted = delivery.text_ok or delivery.delivered_any
        if job:
            if not accepted:
                self._buffer_proactive_retry(
                    job, cause=f"livraison : {delivery.detail or 'cause inconnue'}")
                return False
            self._finish_proactive_delivery(
                job, clock.now(), f"Ta carte de renfo du jour — {title}.",
                platform_messages=self._platform_messages(delivery))
            if not delivery.attachments_ok:
                self._log_incident(
                    f"piece-jointe-proactive:{job.job_id}",
                    "Le texte de la carte de renfo est parti sans son image.")
            return True
        if accepted:
            self._state.record_outbound(
                psid, f"Ta carte de renfo du jour — {title}.", source_key=source_key,
                resolve_questions=False,
                platform_messages=self._platform_messages(delivery))
        return accepted

    def _deliver_strength_card_job(self, job: ProactiveJob) -> None:
        try:
            detail = json.loads(job.detail)
            if not isinstance(detail, dict):
                raise ValueError("objet JSON attendu")
        except (TypeError, ValueError, json.JSONDecodeError):
            self._fail_job(job, "détail de carte de renfo illisible")
            return
        self._deliver_strength_card(
            job.psid, f"proactive:{job.job_id}", detail=detail, job=job)

    def _deliver_missing_cells(self, job: ProactiveJob) -> None:
        """Le repli du soir pour une cellule du registre, rendu sans modèle."""
        try:
            original = json.loads(job.detail)
        except (TypeError, ValueError):
            original = {}
        if not isinstance(original, dict):
            original = {}
        # Les anciens jobs peuvent avoir été créés avant le garde-fou du scheduler. On
        # les expire ici, avant toute livraison, pour qu'une trace de réveil ne soit
        # jamais demandée le soir en /10.
        if original.get("type") == session_kinds.WAKE_TRACE:
            self._complete_job(job)
            log.info("Relance de réveil périmée ignorée : %s", job.job_id or job.kind)
            return
        current = next((entry for entry in self.pending_ledger_cells(clock.today())
                        if str(entry["date"]) == original.get("date")
                        and entry["type"] == original.get("type")), None)
        if current is None:
            self._complete_job(job)
            return
        missing = [column for column in current["missing"]
                   if ledger_model.column_base(column) in {"RPE", "Dlr", "Plaisir"} and not
                   self._state.proactive.marked(
                       "asked:" + ledger_model.cell_key(current, column))]
        if not missing:
            self._complete_job(job)
            return
        detail = json.dumps({**current, "date": str(current["date"]), "missing": missing,
                             "markers": [ledger_model.cell_key(current, column)
                                         for column in missing]})
        closed = (missing_cells_in_notebook(detail) if self._notebook_connected(job.psid)
                  else compose_closed_proactive(job.kind, detail))
        if closed is None:
            self._complete_job(job)
            return
        self._deliver_closed_proactive(job, closed)

    def _deliver_closed_proactive(self, job: ProactiveJob,
                                  message: ClosedProactive) -> None:
        """Livre une initiative entièrement déterminée par ses faits typés."""
        if job.kind == "missed_session" and not self._state.missed_session_is_current(job.job_id):
            self._complete_job(job)
            return
        if message.question and self._question_already_asked(job.psid, message.question):
            # Encore en attente, ou soldée à l'instant (répondue, refusée) : un gabarit
            # n'a pas le contexte pour la reprendre, le coach si.
            log.info("Relance fermée %s ignorée : question déjà posée", job.kind)
            self._complete_job(job)
            return
        builder = self._proactive_builder(job)
        if message.notebook:
            builder.notebook_button(message.text)
        else:
            builder.text(message.text)
        envelope = (builder
                    .meta(kind=job.kind, memory=message.text,
                          markers=list(message.markers),
                          questions=[message.question] if message.question else []).build())
        self._send_proactive(job, envelope)

    def _question_already_asked(self, psid: str, question: str) -> bool:
        try:
            return self._state.question_known(psid, question)
        except Exception:
            # Une mémoire illisible ne prouve pas que la question est neuve, mais se
            # taire ferait perdre une collecte : l'envoi garde son comportement d'avant.
            log.exception("Mémoire des questions illisible avant une relance fermée")
            return False

    def _report_uncertain_delivery(self, source_key: str, delivery) -> None:
        if delivery.uncertain:
            self._log_incident(
                f"livraison-ambigue:{source_key}",
                "Le service a redémarré pendant un envoi. Pour éviter un doublon, "
                "il ne l'a pas rejoué ; sa livraison exacte ne peut pas être confirmée.")

    def _finish_proactive_delivery(self, job: ProactiveJob, sent_at: datetime,
                                   message: str, turn_id: str = "", *,
                                   platform_messages: tuple[tuple[str, str], ...] = (),
                                   questions: tuple[str, ...] = ()) -> None:
        """Acquitte ce qui a réellement été livré, jamais ce que le modèle préparait.

        `turn_id` porte l'angle que le tour a déposé (`fact-write.py angle`) : la trace
        s'écrit après la livraison, longtemps après la frontière de persistance, donc
        c'est ici qu'on relit ce que le modèle avait choisi. Vide pour un envoi que le
        service compose lui-même — il n'a pas d'angle à qualifier.
        """
        self._state.proactive.finish_delivery(job.job_id, sent_at)
        if not message.strip():
            # Un fragment accepté sans texte confirmé n'autorise aucune mémoire
            # inventée. Le job est soldé pour ne pas risquer de le livrer deux fois.
            return
        try:
            self._state.record_outbound(
                job.psid, message, source_key=f"proactive:{job.job_id}", proactive=True,
                platform_messages=platform_messages, turn_id=turn_id,
                questions=questions)
            self._repo.record_proactive_delivery(job, sent_at, message, turn_id)
        except Exception:
            log.exception("Trace du proactif livré impossible à enregistrer")

    @staticmethod
    def _platform_messages(delivery) -> tuple[tuple[str, str], ...]:
        return tuple((item.message_id, item.text) for item in delivery.messages)

    def _proactive_prompt(self, job: ProactiveJob, prompt: str,
                          moment: datetime) -> tuple[str, dict | None, tuple]:
        """Return prompt, strength session and required weeks."""
        current = self._iso_week(moment)
        strength_session: dict | None = None
        required_weeks: tuple[str, ...] = ()
        # Posée avant le retour anticipé : une initiative part sans message de l'athlète,
        # elle a donc encore moins de contexte pour deviner le jour qu'un tour réactif.
        prompt = today_note(moment) + "\n\n" + prompt
        if job.kind in CARNET_KINDS and self._notebook_connected(job.psid):
            prompt = CARNET_NOTE + "\n\n" + prompt
        if job.kind == "weekly_repair":
            week = job.detail or current
            return prompt, None, (week,)
        if job.kind not in {"morning", "weekly"}:
            return prompt, strength_session, required_weeks

        context = self._repo.weekly_plan_context(current)
        prompt = active_revision_note(context) + "\n\n" + prompt
        prompt = BATTERY_NOTE + "\n\n" + prompt
        today = moment.date().isoformat()
        gates = gate_note(*due_gates_by_kind(context, today))
        if gates:
            prompt = gates + "\n\n" + prompt
        if job.kind == "morning":
            packet = self._repo.coaching_context("N1")
            if isinstance(packet, str) and packet:
                prompt = packet + "\n\n" + prompt
            strength_session = strength_due(context, today)
            required_weeks = (current,)
        else:
            # Le bilan reçoit ses calculs faits (dossier) et le paquet N2, qui porte le
            # règlement en vigueur, les échéances et les capacités (audit du 2026-09-27).
            for extra in (self._repo.coaching_context("N2"),
                          self._repo.weekly_dossier(current)):
                if isinstance(extra, str) and extra:
                    prompt = extra + "\n\n" + prompt
            # Le bilan du dimanche PRÉPARE la semaine suivante : il doit prouver les deux.
            required_weeks = (current, self._iso_week(moment, offset_days=7))
        return prompt, strength_session, required_weeks

    def _check_activity_recorded(self, job: ProactiveJob) -> None:
        """Verify that an activity-driven debrief persisted its exact Strava id."""
        if job.kind not in SILENCE_IS_A_FAILURE or not job.detail:
            return
        recorded = self._repo.activity_recorded(job.detail)
        if recorded is None:
            # « Je n'ai pas pu regarder » n'est pas « c'est bon ». Ça ne se dit pas à
            # L'athlète -- il n'y peut rien -- mais ça ne se déguise pas en succès non plus.
            log.error("Contrôle de consignation non concluant pour l'activité %s : "
                      "le débrief est parti sans qu'on sache s'il a consigné", job.detail)
            return
        if recorded:
            return
        self._log_incident(
            f"debrief-non-consigne:{job.detail}",
            "Le débrief est parti, mais le lien Strava de la sortie est introuvable "
            "dans le registre : elle peut être consignée sous une autre identité et "
            "doit être rattachée.")

    def _discard_outbox(self, before: OutboxSnapshot) -> None:
        """Archive les artefacts produits par un tour qui ne sera pas livré."""
        for path in self._outbox.collect_new(before):
            log.info("Image écartée (sortie textuelle absente) : %s", path.name)
            self._outbox.archive(path)

    # ---- commands

    def _run_command(self, psid: str, command: str, *,
                     message_ids: tuple[str, ...] = ()) -> bool:
        suffix = ":".join(message_ids)
        source_key = f"command:{suffix}" if suffix else f"command:{command}:{time.time_ns()}"
        if command in {"/carnet", "/carnet-revoquer"}:
            notebook = self._delivery.notebook
            if notebook is None:
                result = self._client.send_text(psid, "Le carnet est momentanément indisponible.")
            elif command == "/carnet-revoquer":
                notebook.store.revoke(psid)
                result = self._client.send_text(psid, "Les accès au carnet sont révoqués. Tape /carnet pour reconnecter ton appareil.")
            else:
                result = self._client.send_button(
                    psid, "Ton carnet : ta séance du jour, ta semaine et ta course.",
                    notebook.link(psid), "Ouvrir mon carnet")
            return result.ok
        if command == "/renfo":
            return self._deliver_strength_card(psid, source_key)
        if command in ("/start", "/help"):
            text = HELP_TEXT
        elif command == "/clear":
            self._agent_clear(psid)
            text = "Nouvelle conversation. Je repars du contexte frais du vault au prochain message."
        elif command == "/status":
            text = self._status_text(psid)
        elif command == "/cancel":
            text = ("Annulation demandée. Le travail en cours va s'arrêter sans être "
                    "relancé automatiquement."
                    if self._agent.cancel(psid)
                    else "Aucun travail en cours à arrêter.")
        elif command == "/deep":
            text = "Ajoute la question dans le même message : /deep <ta question>"
        else:  # pragma: no cover - WorkQueue filtre les commandes
            return True
        delivery = self._delivery.deliver(
            EnvelopeBuilder(psid, source_key).text(text, burst=False).build())
        self._report_uncertain_delivery(source_key, delivery)
        if delivery.text_ok:
            return True
        if delivery.delivered_any:
            self._log_incident(
                f"commande-partielle:{source_key}",
                f"La commande « {command} » n'a été livrée qu'en partie.")
            return True
        return False

    def _agent_clear(self, psid: str) -> None:
        self._state.clear_session(psid)
        self._state.clear_handoff(psid)

    @staticmethod
    def _duration(seconds: float) -> str:
        minutes = max(0, int(seconds // 60))
        hours, minutes = divmod(minutes, 60)
        return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"

    def _status_text(self, psid: str) -> str:
        activity = self._agent.activity(psid)
        if activity:
            remaining = max(0.0, activity.watchdog_seconds - activity.elapsed)
            running = (
                f"Travail en cours : {activity.model or 'modèle par défaut'}/"
                f"{activity.effort or 'effort par défaut'}, "
                f"depuis {self._duration(activity.elapsed)}, watchdog dans "
                f"{self._duration(remaining)}"
                + (", annulation en cours" if activity.cancelling else "")
            )
        else:
            running = "Aucun travail en cours"
        age = self._state.session_age(psid, self._agent.provider)
        ttl = self._config.session_ttl
        if self._state.session_id(psid, self._agent.provider) and age <= ttl:
            session = (f"Session active, dernier échange il y a {self._duration(age)}, "
                       f"contexte frais dans {self._duration(ttl - age)}")
        else:
            session = "Aucune session active, le prochain message relira le vault"
        routes = (
            ("triage", Route.TRIAGE),
            ("N0", Route.N0),
            ("N1", Route.N1),
            ("N2", Route.N2),
            ("N3", Route.N3),
            ("/deep", Route.DEEP),
            ("proactif lourd", Route.HEAVY),
        )
        routing = "\n".join(
            f"{label} : {self._config.routing.describe(route)}"
            for label, route in routes
        )
        return (
            f"Moteur : {self._agent.provider}\n"
            f"{running}\n"
            f"{session}\n"
            f"Routage :\n{routing}"
        )
