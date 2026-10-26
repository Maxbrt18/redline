"""Serialized in-process queue with Messenger burst debouncing."""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, replace

from .proactive import ProactiveJob


@dataclass(frozen=True)
class DeepRequest:
    psid: str
    text: str
    message_ids: tuple[str, ...] = ()
    attempts: int = 1


@dataclass(frozen=True)
class CommandRequest:
    psid: str
    command: str
    message_ids: tuple[str, ...] = ()
    attempts: int = 1


@dataclass(frozen=True)
class InboundRequest:
    psid: str
    text: str
    message_ids: tuple[str, ...] = ()
    attempts: int = 1


@dataclass(frozen=True)
class AttachmentRequest:
    psid: str
    payload: str
    message_id: str
    attempts: int = 1


@dataclass(frozen=True)
class MaintenanceRequest:
    """Publication Git et synchronisation du vault, quand plus rien n'attend.

    Elles vivent dans le worker parce qu'elles touchent le même dépôt que les tours,
    mais jamais sur le chemin d'une réponse.
    """


WorkItem = (ProactiveJob | DeepRequest | CommandRequest | InboundRequest
            | AttachmentRequest | MaintenanceRequest)


@dataclass
class _InboundBuffer:
    parts: list[str]
    message_ids: list[str]
    first_at: float
    last_at: float


class WorkQueue:
    COMMANDS = frozenset({
        "/start", "/help", "/clear", "/status", "/cancel", "/renfo", "/deep", "/carnet", "/carnet-revoquer",
    })

    def __init__(self, debounce_seconds: float, debounce_max_seconds: float) -> None:
        self._debounce = debounce_seconds
        self._debounce_max = debounce_max_seconds
        self._cv = threading.Condition()
        self._buffers: dict[str, _InboundBuffer] = {}
        self._deep: collections.deque[DeepRequest] = collections.deque()
        self._commands: collections.deque[CommandRequest] = collections.deque()
        self._attachments: collections.deque[AttachmentRequest] = collections.deque()
        self._retries: collections.deque[WorkItem] = collections.deque()
        self._proactive: collections.deque[ProactiveJob] = collections.deque()
        self._maintenance = False
        self._closed = False

    def open(self) -> None:
        with self._cv:
            self._closed = False

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def submit_inbound(self, psid: str, text: str, message_id: str = "") -> tuple[str, ...]:
        """Queue one text and return durable ids intentionally abandoned by `/clear`."""
        stripped = text.strip()
        ids = (message_id,) if message_id else ()
        abandoned: list[str] = []
        with self._cv:
            if stripped in self.COMMANDS:
                if stripped == "/clear":
                    buffered = self._buffers.pop(psid, None)
                    if buffered:
                        abandoned.extend(buffered.message_ids)
                    for request in self._deep:
                        if request.psid == psid:
                            abandoned.extend(request.message_ids)
                    self._deep = collections.deque(
                        request for request in self._deep if request.psid != psid)
                self._commands.append(CommandRequest(psid, stripped, ids))
            elif stripped.startswith("/deep "):
                question = stripped.removeprefix("/deep ").strip()
                buffered = self._buffers.pop(psid, None)
                parts = list(buffered.parts) if buffered else []
                message_ids = list(buffered.message_ids) if buffered else []
                parts.append(question)
                message_ids.extend(ids)
                self._deep.append(DeepRequest(
                    psid, "\n".join(parts), tuple(message_ids)))
            else:
                now = time.monotonic()
                buffer = self._buffers.get(psid)
                if buffer is None:
                    buffer = self._buffers[psid] = _InboundBuffer([], [], now, now)
                buffer.parts.append(text)
                buffer.message_ids.extend(ids)
                buffer.last_at = now
            self._cv.notify()
        return tuple(abandoned)

    def submit_proactive(self, job: ProactiveJob) -> None:
        with self._cv:
            self._proactive.append(job)
            self._cv.notify()

    def submit_maintenance(self) -> None:
        """Demande une maintenance ; plusieurs demandes en attente n'en font qu'une."""
        with self._cv:
            self._maintenance = True
            self._cv.notify()

    def submit_attachment(self, psid: str, payload: str, message_id: str) -> None:
        with self._cv:
            self._attachments.append(AttachmentRequest(psid, payload, message_id))
            self._cv.notify()

    def retry(self, item: WorkItem) -> None:
        """Remet un entrant échoué en file sans repasser par le debounce.

        Le message Meta reste ``pending`` jusqu'à une vraie réponse. La nouvelle
        tentative garde exactement son identité : une réponse déjà calculée pour ces
        messages est renvoyée depuis PostgreSQL, jamais recalculée par le modèle.
        """
        if isinstance(item, (ProactiveJob, MaintenanceRequest)):
            raise TypeError("seuls les entrants se rejouent par cette file")
        with self._cv:
            self._retries.append(replace(item, attempts=item.attempts + 1))
            self._cv.notify()

    def has_pending_inbound(self) -> bool:
        with self._cv:
            return bool(self._buffers or self._deep or self._commands
                        or self._attachments or self._retries)

    def next(self) -> WorkItem | None:
        with self._cv:
            while not self._closed:
                if self._commands:
                    return self._commands.popleft()
                if self._deep:
                    return self._deep.popleft()
                if self._attachments:
                    return self._attachments.popleft()
                if self._retries:
                    return self._retries.popleft()
                psid, wait = self.ready_buffer()
                if psid is not None:
                    buffer = self._buffers.pop(psid)
                    return InboundRequest(
                        psid, "\n".join(buffer.parts), tuple(buffer.message_ids))
                if self._proactive and not self._buffers:
                    return self._proactive.popleft()
                if self._maintenance and not self._buffers:
                    self._maintenance = False
                    return MaintenanceRequest()
                self._cv.wait(timeout=wait)
        return None

    def ready_buffer(self) -> tuple[str | None, float | None]:
        """Return a ripe PSID or the wait until the nearest debounce deadline."""
        now = time.monotonic()
        soonest = None
        for psid, buffer in self._buffers.items():
            deadline = min(buffer.first_at + self._debounce_max,
                           buffer.last_at + self._debounce)
            if now >= deadline:
                return psid, None
            soonest = deadline if soonest is None else min(soonest, deadline)
        if soonest is None:
            return None, None
        return None, max(0.05, soonest - now)
