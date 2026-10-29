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

from redline.integrations import strava
from redline.model import ledger as ledger_model
from redline.model import rulebook, session_kinds

from . import clock
from .config import Config
from .prompts import PROACTIVE_TRIGGERS, build_proactive_prompt, strava_snapshot_note
from .state import StateStore

if TYPE_CHECKING:  # for annotations only: never a runtime import of bot
    from .worker import TurnWorker

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
    # How many times this job has been claimed so far, this one included: retries
    # are capped on this durable counter.
    attempts: int = 1
    # Repetition card of a quality session, computed at Strava capture: the service
    # renders it after the turn instead of leaving it to the model.
    workout_card: dict | None = None
    # Debrief of a quality session: it commits a judgement, so it never runs at the
    # lightest routing level.
    quality: bool = False


class ProactiveScheduler:
    """A tick every TICK seconds evaluates the triggers in decreasing priority. The
    guardrails are applied before waking the agent, and again at send time."""

    TICK = 60
    MORNING_GRACE_HOURS = 3   # the morning note only makes sense shortly after its time
    HEALTH_PING_SECONDS = 300  # heartbeat to healthchecks.io (if configured)
    # Git publication and vault sync, run when the worker is idle.
    MAINTENANCE_SECONDS = 600
    DEBRIEF_MAX_LATE_HOURS = 2   # beyond that, debriefing the session is out of time
    # A capture that fails right after the upload is retried every minute during the
    # first minutes (a transient failure must not cost 5 more minutes), then every
    # 5 min so that a repeating error does not burn the Strava quota.
    CAPTURE_RETRY_FAST = 60
    CAPTURE_RETRY_SLOW = 300
    CAPTURE_FAST_WINDOW = 180
    # Last resort for an empty declarative cell: late enough for the day to have had
    # its natural occasions to claim it (the question normally rides on a message that
    # goes out anyway), early enough to stay inside the polite window. Past midnight
    # the cell leaves its window and becomes `?`: claiming it the next day no longer
    # obtains a memory.
    CELLS_CLAIM_HOUR = 20
    #: The missing wake trace is asked for once in the morning, while the notebook
    #: still takes it (`wake_trace_until` in the rulebook), after the morning message
    #: has had time to be read.
    WAKE_REMINDER_AT = dtime(10, 30)
    # The early weekly review must not double the morning note or the wake request.
    WEEKLY_EARLIEST = dtime(12, 0)

    def __init__(self, bot: TurnWorker, state: StateStore, config: Config) -> None:
        self._bot = bot
        self._state = state
        self._config = config
        self._stop = threading.Event()
        # A signal (Strava webhook, notebook feelings) wakes the loop without waiting
        # for the next tick.
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
        """Run the loop right away (safe from any thread)."""
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
        """Capture an activity as soon as it arrives, without waiting for its debrief.

        The activity enters the ledger immediately: the notebook can ask for its
        feelings while the debrief waits for its signal (renamed title, feelings given,
        message) or its delay. One attempt per activity here: a failure is retried at
        debrief time, with bounded retries.
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
        """An ongoing conversation: message received, turn in flight or recent reply."""
        return self._bot.conversation_active(psid)

    def _materialize_triggers(self, psid: str, now: datetime) -> None:
        today = now.strftime("%Y-%m-%d")
        pro = self._state.proactive

        self._state.materialize_intelligence(now)
        # Closed, idempotent debt: it does not depend on the morning note succeeding,
        # so it also recovers a render missed before a restart or deployment.
        if self._bot is not None:
            self._bot.materialize_strength_card(psid, now)
            # Deterministic invariant: a current week without a valid program has one
            # owner, a single bounded repair job.
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

        wake_until = rulebook.current().parameters.wake_trace_until
        if (self._bot is not None and self._wake_checked_on != today
                and self.WAKE_REMINDER_AT <= now.time() < wake_until):
            # One read per morning is enough: the missing trace can only be filled in
            # afterwards, and delivery re-checks before sending.
            self._wake_checked_on = today
            if self._bot.wake_reminder_due(psid):
                pro.enqueue_job(f"wake-trace:{today}", "wake_trace", "", now.timestamp(),
                                expire_at=clock.combine(now.date(), wake_until).timestamp())

        if now.hour >= self.CELLS_CLAIM_HOUR and self._bot is not None:
            # The Messenger last resort only claims what lives in the athlete's memory.
            # D+/D- are Strava computations: letting them arm this job would produce a
            # generic question after all the feelings were already given.
            claimable = []
            for entry in self._bot.pending_ledger_cells(now.date()):
                # The `wake-trace` is collected on waking, as none/light/clear. Letting
                # it in here would turn it into a `/10` request in the evening, i.e. a
                # memory solicited too late.
                if entry.get("type") == session_kinds.WAKE_TRACE:
                    continue
                missing = [column for column in entry["missing"]
                           if ledger_model.column_base(column) in ledger_model.DECLARED_COLUMNS]
                if missing:
                    claimable.append({**entry, "missing": missing})
            if claimable:
                oldest = claimable[0]
                detail = json.dumps({
                    # The read model already normalizes the date to ISO; ``str`` also
                    # accepts a date object without imposing a second type contract.
                    "date": str(oldest["date"]),
                    "type": oldest["type"],
                    "missing": oldest["missing"],
                    "markers": [ledger_model.cell_key(oldest, column)
                                for column in oldest["missing"]],
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                pro.enqueue_job(f"cells:{today}", "ledger_incomplete", detail,
                                now.timestamp())

        last_contact = max(self._state.last_inbound(psid), pro.last_activity())
        gap_days = (time.time() - last_contact) / 86400
        if gap_days >= self._config.proactive_silence_days:
            pro.enqueue_job(f"silence:{today}", "silence", str(int(gap_days)),
                            now.timestamp())

    def _sunday_settled(self, now: datetime) -> bool:
        """The review goes out as soon as Sunday can no longer change it.

        Settled means: every session of the day done, missed or cancelled, and no
        Strava debrief pending, so the review reads the last activity and comes after
        its debrief. The fixed hour remains the fallback when a session is not settled.
        """
        if self._bot is None or now.time() < self.WEEKLY_EARLIEST:
            return False
        if any(job["kind"] in {"strava_debrief", "strava_debrief_late"}
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
        if record["kind"] in {"strava_debrief", "strava_debrief_late"}:
            try:
                capture = self._state.ingest_strava_activity(
                    int(record["detail"]), fresh_words=True)
                log.info("Strava activity %s captured before debrief: %s",
                         record["detail"], capture["status"])
                snapshot = capture.get("snapshot")
                if not isinstance(snapshot, dict) or not snapshot.get("activity"):
                    raise ValueError("instantané Strava vide après capture")
            except strava.StravaNotFound as exc:
                # Deleted or private: retrying every 5 min only burns API calls and
                # never changes the outcome.
                log.warning("Strava activity %s gone: %s", record["detail"], exc)
                pro.fail_job(record["id"], "activité introuvable sur Strava")
                return None
            except Exception as exc:
                log.error("Strava activity %s not captured (%s): %s",
                          record["detail"], type(exc).__name__, exc)
                # The deadline is anchored at the first failure: beyond it, the activity
                # no longer calls for a hot debrief and the job closes instead of looping.
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
                pro.enqueue_job(deferred_id, "strava_debrief_late", record["detail"],
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
        # A closed initiative (card, next-day, typed reminders) does not even build a
        # prompt: the bot renders it without owning a path to the agent.
        prompt = (build_proactive_prompt(kind, detail)
                  if kind in PROACTIVE_TRIGGERS else "")
        if prompt and isinstance(snapshot, dict):
            prompt = strava_snapshot_note(snapshot) + "\n\n" + prompt
        return ProactiveJob(job_id=record["id"], psid=psid, kind=kind, detail=detail,
                            expire_at=record.get("expire_at") or None,
                            prompt=prompt,
                            priority=bool(record.get("priority")),
                            attempts=int(record.get("attempts", 1)),
                            workout_card=(snapshot.get("quality_card")
                                          if isinstance(snapshot, dict) else None),
                            quality=bool(isinstance(snapshot, dict)
                                         and snapshot.get("quality")))
