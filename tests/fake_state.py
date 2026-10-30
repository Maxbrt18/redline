"""In-memory double reserved for unit tests; production only has PostgreSQL."""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime


class FakeProactiveState:
    def __init__(self) -> None:
        self._jobs: list[dict] = []
        self._markers: set[str] = set()
        self._last_activity = 0.0
        self._budget_day = ""
        self._sent_count = 0
        self._last_sent = 0.0

    def record_activity(self, activity_id: str, due: float) -> None:
        self._last_activity = time.time()
        self.enqueue_job(f"strava:{activity_id}", "strava_debrief", str(activity_id), due,
                         priority=True)

    def last_activity(self) -> float:
        return self._last_activity

    def enqueue_job(self, job_id: str, kind: str, detail: str, due: float,
                    *, expire_at: float = 0.0, priority: bool = False) -> bool:
        if any(job["id"] == job_id for job in self._jobs):
            return False
        self._jobs.append({"id": job_id, "kind": kind, "detail": detail, "due": due,
                           "expire_at": expire_at, "priority": priority,
                           "status": "pending", "attempts": 0, "last_error": ""})
        return True

    def claim_due_job(self, now: float, *, allow_ordinary: bool = True):
        self.expire_jobs(now)
        candidates = [job for job in self._jobs if job["status"] == "pending"
                      and job["due"] <= now and (allow_ordinary or job["priority"])]
        candidates.sort(key=lambda job: (not job["priority"], job["due"]))
        for job in candidates:
            job["status"] = "running"
            job["attempts"] += 1
            return dict(job)
        return None

    def claim_job(self, job_id: str):
        job = next((job for job in self._jobs
                    if job["id"] == job_id and job["status"] == "pending"), None)
        if job is None:
            return None
        job["status"] = "running"
        job["attempts"] += 1
        return dict(job)

    def hasten_job(self, job_id: str, due: float) -> bool:
        job = next((job for job in self._jobs if job["id"] == job_id
                    and job["status"] == "pending" and job["due"] > due), None)
        if job is None:
            return False
        job["due"] = due
        return True

    def expire_jobs(self, now: float) -> int:
        expired = [job for job in self._jobs if job["status"] == "pending"
                   and job["expire_at"] and now > job["expire_at"]]
        for job in expired:
            job.update(status="failed", last_error="expiré avant livraison")
        return len(expired)

    def complete_job(self, job_id: str) -> None:
        next(job for job in self._jobs if job["id"] == job_id)["status"] = "done"

    def retry_job(self, job_id: str, due: float, expire_at: float) -> None:
        job = next(job for job in self._jobs if job["id"] == job_id
                   and job["status"] in {"pending", "running"})
        job.update(status="pending", due=due, expire_at=expire_at)

    def reopen_job(self, job_id: str, due: float, expire_at: float) -> None:
        job = next(job for job in self._jobs
                   if job["id"] == job_id and job["status"] in {"done", "failed"})
        job.update(status="pending", due=due, expire_at=expire_at, attempts=0,
                   last_error="")

    def release_job(self, job_id: str, delay: float = 60.0, *,
                    expire_at: float = 0.0) -> bool:
        job = next(job for job in self._jobs if job["id"] == job_id)
        if job["status"] != "running":
            return False
        due = time.time() + delay
        expires = job["expire_at"] or expire_at
        if expires and due > expires:
            job.update(status="failed", last_error="expiré avant livraison")
            return False
        job.update(status="pending", due=due, expire_at=expires,
                   attempts=max(0, job["attempts"] - 1))
        return True

    def fail_job(self, job_id: str, cause: str) -> None:
        job = next(job for job in self._jobs if job["id"] == job_id)
        if job["status"] in {"pending", "running"}:
            job.update(status="failed", last_error=cause)

    def recover_running_jobs(self) -> int:
        recovered = 0
        for job in self._jobs:
            if job["status"] == "running":
                job.update(status="pending", due=min(job["due"], time.time()))
                recovered += 1
        return recovered

    def jobs(self, *, active_only: bool = False) -> list[dict]:
        rows = self._jobs if not active_only else [
            job for job in self._jobs if job["status"] in {"pending", "running"}]
        return [dict(job) for job in rows]

    def marked(self, name: str) -> bool:
        return name in self._markers

    def mark(self, name: str) -> None:
        self._markers.add(name)

    def would_pass(self, now: datetime, config, ignore_gap: bool = False) -> bool:
        day = now.date().isoformat()
        last = self._last_sent if self._budget_day == day else 0.0
        return (config.in_active_window(now.time())
                and (ignore_gap or now.timestamp() - last
                     >= config.proactive_min_gap_hours * 3600))

    def finish_delivery(self, job_id: str, now: datetime) -> None:
        day = now.date().isoformat()
        if self._budget_day != day:
            self._budget_day, self._sent_count = day, 0
        self._sent_count += 1
        self._last_sent = now.timestamp()
        if job_id:
            self.complete_job(job_id)


class FakeStateStore:
    def __init__(self) -> None:
        self.proactive = FakeProactiveState()
        self.turns: list = []
        self._sessions: dict[str, dict] = {}
        self._handoffs: dict[str, str] = {}
        self._inbound: dict[str, float] = {}
        self._plans: dict[str, tuple[str, int]] = {}
        self._patience = 0.0
        self._inbound_messages: dict[str, dict] = {}
        self._session_cycles: list[dict] = []
        self._conversation: list[dict] = []
        self._questions: set[str] = set()
        self._strength_links: dict[str, dict] = {}
        self._deliveries: dict[str, dict] = {}

    def register_turn(self, turn_id: str, psid: str) -> None:
        pass

    def record_turn(self, identity) -> None:
        self.turns.append(identity)

    def materialize_intelligence(self, now: datetime) -> int:
        return 0

    def missed_session_is_current(self, job_id: str) -> bool:
        return any(job_id == f"missed-session:{row['id']}" and row["state"] == "missed"
                   for row in self._session_cycles)

    def record_outbound(self, psid: str, text: str, *, source_key: str,
                        proactive: bool = False,
                        resolve_questions: bool = True,
                        platform_messages: tuple[tuple[str, str], ...] = (),
                        turn_id: str = "",
                        questions: tuple[str, ...] = ()) -> None:
        asked = tuple(item for item in questions if item in text)
        self._questions.update(asked)
        self._conversation.append({"psid": psid, "text": text, "source_key": source_key,
                                   "proactive": proactive,
                                   "resolve_questions": resolve_questions,
                                   "platform_messages": platform_messages,
                                   "questions": asked})

    def question_known(self, psid: str, text: str) -> bool:
        return text in self._questions

    def begin_outbound_delivery(self, source_key: str, psid: str,
                                payload_digest: str, payload=None) -> str:
        row = self._deliveries.get(source_key)
        if row is None:
            self._deliveries[source_key] = {
                "psid": psid, "digest": payload_digest, "status": "sending",
                "attempts": 1, "payload": payload}
            return "send"
        if row["psid"] != psid:
            raise ValueError("collision d'enveloppe sortante")
        if row["status"] == "delivered":
            return "delivered"
        if row["status"] == "sending":
            return "uncertain"
        row.update(digest=payload_digest, status="sending", payload=payload,
                   attempts=row["attempts"] + (row["status"] != "prepared"))
        return "send"

    def prepare_outbound_delivery(self, source_key: str, psid: str,
                                  payload_digest: str, payload: dict) -> bool:
        row = self._deliveries.get(source_key)
        if row is not None and row["status"] not in {"prepared", "failed"}:
            return False
        self._deliveries[source_key] = {
            "psid": psid, "digest": payload_digest, "status": "prepared",
            "attempts": (row or {}).get("attempts", 1), "payload": payload}
        return True

    def stored_outbound_delivery(self, source_key: str):
        row = self._deliveries.get(source_key)
        if row is None:
            return None
        return {"psid": row["psid"], "status": row["status"],
                "payload": row.get("payload")}

    def discard_outbound_delivery(self, source_key: str) -> None:
        row = self._deliveries.get(source_key)
        if row is not None and row["status"] in {"prepared", "failed"}:
            self._deliveries.pop(source_key)

    def expire_stale_questions(self, psid: str, *, older_than_days: int = 14) -> int:
        return 0

    def finish_outbound_delivery(self, source_key: str, *, delivered: bool,
                                 error: str = "") -> None:
        self._deliveries[source_key].update(
            status="delivered" if delivered else "failed", error=error)

    def reply_context(self, message_ids: tuple[str, ...]) -> str:
        references = []
        by_platform_id = {
            message_id: (body, "coach")
            for row in self._conversation
            for message_id, body in row.get("platform_messages", ())
        }
        for message_id in message_ids:
            row = self._inbound_messages.get(message_id) or {}
            reply_to = row.get("reply_to_message_id")
            if not reply_to:
                continue
            body, owner = by_platform_id.get(reply_to, ("", ""))
            if not body:
                source = self._inbound_messages.get(reply_to) or {}
                body, owner = source.get("body", ""), "Sam"
            references.append(f"{owner}: {body}" if body else "référence inconnue")
        return ("[RÉPONSE MESSENGER CIBLÉE — invisible]\n" + "\n".join(references)
                if references else "")

    def issue_strength_link(self, psid: str, session: dict) -> str:
        from redline.service import strength
        token = secrets.token_urlsafe(strength.TOKEN_BYTES)
        self._strength_links[hashlib.sha256(token.encode()).hexdigest()] = {
            "psid": psid, "session": strength.expiring_snapshot(session),
            "revoked": False}
        return token

    def resolve_strength_link(self, psid: str, token: str) -> dict:
        row = self._strength_links.get(hashlib.sha256(token.encode()).hexdigest())
        if not row or row["psid"] != psid:
            return {"status": "missing"}
        if row["revoked"]:
            return {"status": "revoked"}
        if row["session"]["expires_at"] <= int(time.time()):
            return {"status": "expired"}
        return {"status": "active", "session": dict(row["session"])}

    def revoke_strength_link(self, psid: str, token: str) -> bool:
        row = self._strength_links.get(hashlib.sha256(token.encode()).hexdigest())
        if not row or row["psid"] != psid or row["revoked"]:
            return False
        row["revoked"] = True
        return True

    def relationship_context(self, psid: str, *, threads: bool = True) -> str:
        return ""

    def correction_context(self, text: str) -> str:
        return ""

    def ingest_strava_activity(self, activity_id: int, *, fresh_words: bool = False) -> dict:
        return {
            "activity_id": activity_id,
            "status": "matched",
            "snapshot": {
                "source": "strava_capture",
                "activity": {"id": activity_id, "name": "easy", "sport": "Run"},
                "laps": [],
                "matching": {"status": "matched"},
                "enrichments": {},
            },
        }

    def session_id(self, psid: str, provider: str):
        row = self._sessions.get(psid)
        return row["id"] if row and row["provider"] == provider else None

    def session_age(self, psid: str, provider: str) -> float:
        row = self._sessions.get(psid)
        return time.time() - row["last"] if row and row["provider"] == provider else float("inf")

    def session_prompt(self, psid: str, provider: str) -> str:
        row = self._sessions.get(psid)
        return row["prompt"] if row and row["provider"] == provider else ""

    def set_session(self, psid: str, provider: str, session_id: str,
                    prompt_digest: str = "") -> None:
        self._sessions[psid] = {"provider": provider, "id": session_id,
                                "last": time.time(), "prompt": prompt_digest}

    def section(self, name: str):
        sections = {"sessions": self._sessions, "inbound": self._inbound}
        if name not in sections:
            raise KeyError(name)
        return sections[name]

    def clear_session(self, psid: str, provider=None) -> None:
        row = self._sessions.get(psid)
        if row and (provider is None or row["provider"] == provider):
            self._sessions.pop(psid)

    def handoff(self, psid: str) -> str:
        return self._handoffs.get(psid, "")

    def set_handoff(self, psid: str, text: str) -> None:
        self._handoffs[psid] = "\n\n".join(filter(None, (self.handoff(psid), text)))[-20_000:]

    def clear_handoff(self, psid: str) -> None:
        self._handoffs.pop(psid, None)

    def mark_inbound(self, psid: str) -> None:
        self._inbound[psid] = time.time()

    def claim_inbound(self, message_id: str, psid: str, kind: str, body: str, *,
                      reply_to_message_id: str = "") -> bool:
        if message_id in self._inbound_messages:
            return False
        self._inbound_messages[message_id] = {
            "message_id": message_id, "psid": psid, "kind": kind, "body": body,
            "reply_to_message_id": reply_to_message_id or None, "status": "pending",
            "received_at": time.time()}
        return True

    def inbound_received_at(self, message_ids: tuple[str, ...]):
        stamps = [self._inbound_messages[message_id]["received_at"]
                  for message_id in message_ids
                  if message_id in self._inbound_messages]
        return min(stamps) if stamps else None

    def pending_inbound(self) -> list[dict]:
        return [dict(row) for row in self._inbound_messages.values()
                if row["status"] == "pending"]

    def complete_inbound(self, message_ids: tuple[str, ...]) -> None:
        for message_id in message_ids:
            row = self._inbound_messages[message_id]
            row["status"] = "done"
            if row["kind"] == "attachment":
                row["body"] = ""

    def last_inbound(self, psid: str) -> float:
        return self._inbound.get(psid, 0.0)

    def within_24h(self, psid: str) -> bool:
        return time.time() - self.last_inbound(psid) < 86400

    def claim_patience(self, min_gap_seconds: float) -> bool:
        now = time.time()
        if now - self._patience < min_gap_seconds:
            return False
        self._patience = now
        return True

    def owe_plan(self, psid: str, week: str, revision: int) -> None:
        self._plans[psid] = (week, revision)

    def plan_owed(self, psid: str) -> tuple[str, int]:
        return self._plans.get(psid, ("", 0))

    def settle_plan(self, psid: str) -> None:
        self._plans.pop(psid, None)
