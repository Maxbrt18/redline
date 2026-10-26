"""Proactivity: the coach takes the initiative like a human thinking of their athlete.

The scheduler detects in Python (cheap) when a trigger fires. Closed triggers are
rendered without a model; the others delegate the judgement and the writing to the
configured agent, which may decide to stay silent (typed `send=false`). The trigger
prompts live in prompts.py.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from typing import TYPE_CHECKING

import requests

from scripts import strava
from scripts.model import ledger as ledger_model

from . import clock
from .config import Config
from .notebook_board import WAKE_ASK_UNTIL
from .prompts import PROACTIVE_TRIGGERS, build_proactive_prompt, strava_snapshot_note
from .state import StateStore

if TYPE_CHECKING:  # for annotations only: never a runtime import of bot
    from .bot import CoachBot

log = logging.getLogger("redline")

ACTIVE_SILENCE_SECONDS = 8 * 60

HEAVY_KINDS = frozenset({"weekly", "weekly_repair", "monthly", "decision_challenge",
                         "premortem"})

@dataclass
class ProactiveJob:
    psid: str
    kind: str
    prompt: str
    job_id: str = ""
    detail: str = ""
    # Absolute expiry (epoch) when the job comes from a retry: beyond it, no
    # longer relevant. None for a fresh trigger (computed when queued).
    expire_at: float | None = None
    # Time-sensitive trigger: exempt from the minimum gap between two proactive
    # messages.
    priority: bool = False
    # Combien de fois ce travail a déjà été réclamé, celle-ci comprise : les reprises
    # sont plafonnées sur ce compteur durable.
    attempts: int = 1
    # La carte des répétitions d'une séance de qualité, calculée à la capture Strava :
    # le service la rend après le tour, sans la laisser au choix du modèle.
    workout_card: dict | None = None
    # Débrief d'une séance de qualité : il engage un jugement et ne tourne pas en N1.
    quality: bool = False


class ProactiveScheduler:
    """A tick every TICK seconds evaluates the triggers in decreasing priority. The
    guardrails are applied before waking the agent, and again at send time."""

    TICK = 60
    MORNING_GRACE_HOURS = 3   # the morning note only makes sense shortly after its time
    HEALTH_PING_SECONDS = 300  # heartbeat to healthchecks.io (if configured)
    # Publication Git et synchronisation du vault quand le worker est libre.
    MAINTENANCE_SECONDS = 600
    DEBRIEF_MAX_LATE_HOURS = 2   # beyond that, debriefing the session is out of time
    # Une capture ratée juste après l'upload se reprend à la minute pendant les
    # premières minutes (un raté passager ne doit pas coûter 5 min de plus), puis
    # toutes les 5 min : une erreur qui se répète n'use pas le quota Strava.
    CAPTURE_RETRY_FAST = 60
    CAPTURE_RETRY_SLOW = 300
    CAPTURE_FAST_WINDOW = 180
    # Dernier recours pour une cellule déclarative vide : assez tard pour que la
    # journée ait eu ses occasions naturelles de la réclamer (la question s'accroche
    # normalement à un message qui part de toute façon), assez tôt pour rester dans
    # la fenêtre de politesse. Au-delà de minuit la cellule sort de sa fenêtre et
    # devient `?` : la réclamer le lendemain n'obtient plus un souvenir (2026-08-06).
    CELLS_CLAIM_HOUR = 20
    #: La trace du réveil que le carnet n'a pas reçue se rappelle le matin même, tant
    #: que le carnet la prend encore (jusqu'à WAKE_ASK_UNTIL). Jamais le soir : c'est
    #: un souvenir trop tardif (correction du 2026-09-15). 10 h 30 laisse au mot du
    #: matin (07:45) le temps d'être lu sans relance immédiate.
    WAKE_REMINDER_AT = dtime(10, 30)
    # Le bilan anticipé ne double pas le mot du matin ni la demande du réveil.
    WEEKLY_EARLIEST = dtime(12, 0)

    def __init__(self, bot: CoachBot, state: StateStore, config: Config) -> None:
        self._bot = bot
        self._state = state
        self._config = config
        self._stop = threading.Event()
        # Un signal (webhook Strava, ressentis du carnet) réveille la boucle sans
        # attendre le prochain tick.
        self._nudge = threading.Event()
        self._thread: threading.Thread | None = None
        self._captured_on_arrival: set[str] = set()
        self._last_health_ping = 0.0
        self._last_maintenance = 0.0
        self._last_block = ""  # last budget-block reason logged (log only on change)
        self._wake_checked_on = ""  # one board read per morning, not one per tick

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="proactive")
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        self._nudge.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def nudge(self) -> None:
        """Fait tourner la boucle tout de suite (sûr depuis n'importe quel thread)."""
        self._nudge.set()

    def _loop(self) -> None:
        while True:
            self._nudge.wait(self.TICK)
            self._nudge.clear()
            if self._stop.is_set():
                return
            self._ping_healthcheck()  # independent of proactivity: it is the service's pulse
            try:
                self._tick()
            except Exception:
                log.exception("Proactive tick failed")

    def _ping_healthcheck(self) -> None:
        """Outgoing heartbeat: healthchecks.io alerts the athlete if the pings stop
        (service dead, VPS down). Silent and harmless otherwise."""
        url = self._config.healthcheck_ping_url
        if not url:
            return
        now = time.monotonic()
        if now - self._last_health_ping < self.HEALTH_PING_SECONDS:
            return
        self._last_health_ping = now
        try:
            requests.get(url, timeout=10)
        except requests.RequestException as exc:
            log.warning("Healthcheck ping failed: %s", exc)

    def _tick(self) -> None:
        if not self._config.proactive_enabled:
            return
        psid = self._config.allowed_psid
        if not psid:
            return  # without a known athlete (PSID), no initiative possible
        now = clock.now()
        if time.monotonic() - self._last_maintenance >= self.MAINTENANCE_SECONDS:
            self._last_maintenance = time.monotonic()
            self._bot.request_maintenance()
        # Detection is independent from delivery guardrails, including an active
        # conversation. Stable ids make this safe to call on every tick.
        self._materialize_triggers(psid, now)
        self._capture_arrivals()
        self._state.proactive.expire_jobs(now.timestamp())
        if self._user_active(psid):
            return
        pro = self._state.proactive
        # Two gates: the morning start decides whether ANYTHING can go out, the full
        # one (+ minimum gap) whether an ordinary trigger can.
        if not pro.would_pass(now, self._config, ignore_gap=True):
            self._log_block("active window")
            return
        gap_ok = pro.would_pass(now, self._config)
        if not gap_ok:
            self._log_block("minimum gap (priority triggers still pass)")
        else:
            self._last_block = ""
        job = self._claim_due_trigger(psid, now, allow_ordinary=gap_ok)
        if job:
            log.info("Proactive trigger: %s%s", job.kind, " (priority)" if job.priority else "")
            self._bot.submit_proactive(job)

    def _capture_arrivals(self) -> None:
        """Capture une sortie dès son arrivée, sans attendre son débrief.

        La sortie entre ainsi au registre tout de suite : le carnet peut demander ses
        ressentis pendant que le débrief attend son signal (titre renommé, ressentis
        donnés, message) ou son délai. Une seule tentative ici par activité : un échec
        se reprend au moment du débrief, avec ses reprises bornées.
        """
        for job in self._state.proactive.jobs(active_only=True):
            if (job["kind"] != "strava_debrief" or job["status"] != "pending"
                    or job["id"] in self._captured_on_arrival):
                continue
            self._captured_on_arrival.add(job["id"])
            try:
                capture = self._state.ingest_strava_activity(int(job["detail"]))
                log.info("Strava activity %s captured on arrival: %s",
                         job["detail"], capture["status"])
            except Exception as exc:
                log.warning("Strava activity %s not captured on arrival (%s): %s",
                            job["detail"], type(exc).__name__, exc)

    def _log_block(self, reason: str) -> None:
        """The budget silently swallowing a trigger is invisible in the logs, which
        makes any diagnosis impossible. Logged once per change, not every tick."""
        if reason != self._last_block:
            self._last_block = reason
            log.info("Proactive budget blocking: %s", reason)

    def _user_active(self, psid: str) -> bool:
        """Une conversation en cours : message reçu, tour en vol ou réponse récente."""
        return self._bot.conversation_active(psid)

    def _materialize_triggers(self, psid: str, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        pro = self._state.proactive

        self._state.materialize_intelligence(now)
        # Dette fermée et idempotente : elle ne dépend pas de la réussite du mot du
        # matin et récupère donc aussi un rendu manqué avant un redémarrage/déploiement.
        if self._bot is not None:
            self._bot.materialize_strength_card(psid, now)
            # Invariant déterministe : une semaine courante sans programme valide a un
            # propriétaire, un job de réparation unique et borné.
            self._bot.materialize_week_repair(now)

        # Materialize periodic triggers with stable ids. Enqueue is idempotent, so
        # detection never consumes execution and a restart cannot lose the job.
        weekly = self._config.proactive_weekly
        if weekly and now.weekday() == 6 and (
                now.time() >= weekly or self._sunday_settled(now)):
            key = now.strftime("%G-W%V")
            pro.enqueue_job(f"weekly:{key}", "weekly", "", now.timestamp())

        monthly = self._config.proactive_monthly
        if monthly and now.day == 1 and now.time() >= monthly:
            key = now.strftime("%Y-%m")
            pro.enqueue_job(f"monthly:{key}", "monthly", "", now.timestamp())

        morning = self._config.proactive_morning
        if morning:
            grace_end = clock.combine(now.date(), morning).timestamp() + \
                self.MORNING_GRACE_HOURS * 3600
            if now.time() >= morning and now.timestamp() <= grace_end:
                pro.enqueue_job(f"morning:{today}", "morning", "", now.timestamp(),
                                expire_at=grace_end)

        if (self._bot is not None and self._wake_checked_on != today
                and self.WAKE_REMINDER_AT <= now.time() < WAKE_ASK_UNTIL):
            # Une lecture par matin suffit : la trace manquante ne peut qu'être remplie
            # ensuite, et la livraison revérifie avant d'envoyer.
            self._wake_checked_on = today
            if self._bot.wake_reminder_due(psid):
                pro.enqueue_job(f"trace-reveil:{today}", "trace_reveil", "", now.timestamp(),
                                expire_at=clock.combine(now.date(), WAKE_ASK_UNTIL).timestamp())

        if now.hour >= self.CELLS_CLAIM_HOUR and self._bot is not None:
            # Le dernier recours Messenger ne réclame que ce qui vit dans la mémoire
            # de l'athlète. D+/D- sont des calculs Strava : les laisser armer ce job
            # produisait une question générique après que tous les ressentis avaient
            # déjà été donnés (2026-08-30).
            claimable = []
            for entry in self._bot.pending_ledger_cells(now.date()):
                # La trace `reveil` se recueille au réveil, en aucune/légère/nette.
                # La laisser entrer ici la transforme en demande `/10` le soir,
                # puis en souvenir sollicité trop tard (2026-09-15).
                if entry.get("type") == "reveil":
                    continue
                missing = [column for column in entry["missing"]
                           if ledger_model.column_base(column) in {"RPE", "Dlr", "Plaisir"}]
                if missing:
                    claimable.append({**entry, "missing": missing})
            if claimable:
                oldest = claimable[0]
                detail = json.dumps({
                    # Le read model normalise déjà la date en ISO ; ``str`` accepte
                    # aussi un objet date sans imposer un second contrat de type.
                    "date": str(oldest["date"]),
                    "type": oldest["type"],
                    "missing": oldest["missing"],
                    "markers": [ledger_model.cell_key(oldest, column)
                                for column in oldest["missing"]],
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                pro.enqueue_job(f"cells:{today}", "registre_incomplet", detail,
                                now.timestamp())

        last_contact = max(self._state.last_inbound(psid), pro.last_activity())
        gap_days = (time.time() - last_contact) / 86400
        if gap_days >= self._config.proactive_silence_days:
            pro.enqueue_job(f"silence:{today}", "silence", str(int(gap_days)),
                            now.timestamp())

    def _sunday_settled(self, now: datetime) -> bool:
        """Le bilan part dès que le dimanche ne peut plus le changer (2026-10-18).

        Attendre 19 h laissait l'athlète sans semaine suivante tout l'après-midi. Soldé
        veut dire : chaque séance du jour faite, manquée ou annulée, et aucun débrief
        Strava en attente, pour que le bilan lise la dernière sortie et passe après
        son débrief. L'heure fixe reste le repli quand une séance n'est pas soldée.
        """
        if self._bot is None or now.time() < self.WEEKLY_EARLIEST:
            return False
        if any(job["kind"] in {"strava_debrief", "strava_debrief_differe"}
               for job in self._state.proactive.jobs(active_only=True)):
            return False
        return self._bot.day_settled(now.date())

    def _claim_due_trigger(self, psid: str, now: datetime,
                           allow_ordinary: bool = True) -> ProactiveJob | None:
        pro = self._state.proactive

        record = pro.claim_due_job(now.timestamp(), allow_ordinary=allow_ordinary)
        if record is None:
            return None

        snapshot = None
        if record["kind"] in {"strava_debrief", "strava_debrief_differe"}:
            try:
                capture = self._state.ingest_strava_activity(
                    int(record["detail"]), fresh_words=True)
                log.info("Strava activity %s captured before debrief: %s",
                         record["detail"], capture["status"])
                snapshot = capture.get("snapshot")
                if not isinstance(snapshot, dict) or not snapshot.get("activity"):
                    raise ValueError("instantané Strava vide après capture")
            except strava.StravaNotFound as exc:
                # Supprimée ou privée : la reprise toutes les 5 min a coûté 1 500
                # appels par activité entre le 17 et le 25/09, sans rien changer.
                log.warning("Strava activity %s gone: %s", record["detail"], exc)
                pro.fail_job(record["id"], "activité introuvable sur Strava")
                return None
            except Exception as exc:
                log.error("Strava activity %s not captured (%s): %s",
                          record["detail"], type(exc).__name__, exc)
                # L'échéance s'ancre au premier échec : au-delà, la sortie n'appelle
                # plus un débrief à chaud et le job se solde au lieu de boucler.
                window = self.DEBRIEF_MAX_LATE_HOURS * 3600
                failing_for = (window - (record["expire_at"] - time.time())
                               if record.get("expire_at") else 0.0)
                delay = (self.CAPTURE_RETRY_FAST if failing_for < self.CAPTURE_FAST_WINDOW
                         else self.CAPTURE_RETRY_SLOW)
                pro.release_job(record["id"], delay=delay, expire_at=time.time() + window)
                return None
        if record["kind"] == "strava_debrief":
            late = now.timestamp() - record["due"]
            if late > self.DEBRIEF_MAX_LATE_HOURS * 3600:
                due_at = clock.fromtimestamp(record["due"]).time()
                if self._config.in_active_window(due_at):
                    pro.fail_job(record["id"], "débrief trop tardif pour partir à chaud")
                    log.error("Incident interne « debrief-abandonne:%s » : débrief non "
                              "envoyé, %.1f h de retard pendant la plage d'envoi",
                              record["detail"], late / 3600)
                    return None
                pro.complete_job(record["id"])
                deferred_id = record["id"] + ":deferred"
                pro.enqueue_job(deferred_id, "strava_debrief_differe", record["detail"],
                                now.timestamp(), priority=True)
                record = pro.claim_due_job(now.timestamp(), allow_ordinary=False)
                if record is None:
                    return None

        if snapshot is not None:
            record["snapshot"] = snapshot

        return self._job(psid, record)

    @classmethod
    def _job(cls, psid: str, record: dict) -> ProactiveJob:
        kind, detail = record["kind"], record.get("detail", "")
        snapshot = record.get("snapshot")
        # Une initiative fermée (carte, J+1, relances typées) ne construit même pas de
        # prompt : le bot la rend sans posséder de chemin vers l'agent.
        prompt = (build_proactive_prompt(kind, detail)
                  if kind in PROACTIVE_TRIGGERS else "")
        if prompt and isinstance(snapshot, dict):
            prompt = strava_snapshot_note(snapshot) + "\n\n" + prompt
        return ProactiveJob(job_id=record["id"], psid=psid, kind=kind, detail=detail,
                            expire_at=record.get("expire_at") or None,
                            prompt=prompt,
                            priority=bool(record.get("priority")),
                            attempts=int(record.get("attempts", 1)),
                            workout_card=(snapshot.get("repetitions")
                                          if isinstance(snapshot, dict) else None),
                            quality=bool(isinstance(snapshot, dict)
                                         and snapshot.get("qualite")))
