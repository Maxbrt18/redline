"""Composition and delivery of Messenger outputs.

The transport knows how to send a text or an image. This module carries the business
unit: a complete, ordered output, with a distinct verdict for its text and its
artifacts. The bot can thus acknowledge a job from what actually crossed the
transport, without re-coding partial successes for each trigger.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from redline.model.vocabulary import RESENDABLE_DELIVERIES, DeliveryStatus

from .messenger import DeliveredMessage, DeliveryResult, MessengerClient
from .notebook import NotebookService
from .outbox import Outbox, OutboxSnapshot
from .state import Disposition, StateStore

log = logging.getLogger("redline")


@dataclass(frozen=True)
class TextPart:
    text: str
    mode: Literal["text", "burst"] = "burst"
    pause_before: bool = False
    #: The answers the athlete may tap under the text, each a (title, payload).
    replies: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ImagePart:
    path: Path
    #: The strength card's (bubble text, session snapshot): it files the card as a strength
    #: session in the notebook; nothing is sent from it.
    link: tuple[str, dict] | None = None


OutboundPart = TextPart | ImagePart


@dataclass(frozen=True)
class OutboundEnvelope:
    psid: str
    source_key: str
    parts: tuple[OutboundPart, ...]
    messaging_type: str = "RESPONSE"
    tag: str | None = None
    # What the bot needs to acknowledge the send if it is replayed later (turn, text to
    # remember, revision shown). Excluded from the digest: it is not content.
    meta: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class StoredDelivery:
    """An already computed envelope, as PostgreSQL stores it."""

    source_key: str
    psid: str
    status: str
    payload: dict | None

    @property
    def meta(self) -> dict:
        return dict((self.payload or {}).get("meta") or {})

    @property
    def resendable(self) -> bool:
        """Computed and never accepted by Meta: it can be resent as is."""
        return self.status in RESENDABLE_DELIVERIES and bool(
            (self.payload or {}).get("parts"))


def envelope_payload(envelope: OutboundEnvelope) -> dict:
    """The durable form of an envelope.

    The notebook keeps the images separately before sending. A transport retry only
    resends the text; the cards remain available in the notebook.
    """
    parts: list[dict] = []
    for part in envelope.parts:
        if isinstance(part, TextPart):
            parts.append({"kind": "text", "text": part.text, "mode": part.mode,
                          "pause_before": part.pause_before,
                          "replies": [list(reply) for reply in part.replies]})
    return {"messaging_type": envelope.messaging_type, "tag": envelope.tag,
            "parts": parts, "meta": dict(envelope.meta)}


def envelope_from_payload(stored: StoredDelivery) -> OutboundEnvelope:
    payload = stored.payload or {}
    parts: list[OutboundPart] = []
    for item in payload.get("parts", ()):
        if item.get("kind") == "text":
            parts.append(TextPart(str(item["text"]), item.get("mode", "burst"),
                                  bool(item.get("pause_before")),
                                  tuple((str(title), str(payload))
                                        for title, payload in item.get("replies") or ())))
        elif item.get("kind") == "notebook_button":
            # A stored envelope may still carry the retired notebook button: its text goes
            # out alone.
            parts.append(TextPart(str(item["text"]), "text"))
    if not parts:
        raise ValueError(f"stored envelope without content: {stored.source_key}")
    return OutboundEnvelope(stored.psid, stored.source_key, tuple(parts),
                            str(payload.get("messaging_type") or "RESPONSE"),
                            payload.get("tag"), dict(payload.get("meta") or {}))


@dataclass(frozen=True)
class DeliveryReceipt:
    text_ok: bool
    attachments_ok: bool
    delivered: int = 0
    detail: str = ""
    messages: tuple[DeliveredMessage, ...] = ()
    replayed: bool = False
    uncertain: bool = False

    @property
    def delivered_any(self) -> bool:
        return self.delivered > 0


@dataclass
class EnvelopeBuilder:
    """Small mutable assembler; the envelope it produces stays immutable."""

    psid: str
    source_key: str
    messaging_type: str = "RESPONSE"
    tag: str | None = None
    parts: list[OutboundPart] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def meta(self, **values: Any) -> EnvelopeBuilder:
        self.metadata.update(values)
        return self

    def text(self, value: str, *, burst: bool = True, pause_before: bool = False,
             replies: tuple[tuple[str, str], ...] = ()) -> EnvelopeBuilder:
        if value.strip():
            self.parts.append(TextPart(value, "text" if replies else "burst" if burst
                                       else "text", pause_before, replies))
        return self

    def images(self, outbox: Outbox, before: OutboxSnapshot) -> EnvelopeBuilder:
        for path in outbox.collect_new(before):
            self.parts.append(ImagePart(path, outbox.link_for(path)))
        return self

    def image(self, outbox: Outbox, path: Path) -> EnvelopeBuilder:
        """Add the artifact explicitly produced by a typed renderer."""
        self.parts.append(ImagePart(path, outbox.link_for(path)))
        return self

    def build(self) -> OutboundEnvelope:
        if not self.parts:
            raise ValueError("an outbound envelope cannot be empty")
        return OutboundEnvelope(
            self.psid, self.source_key, tuple(self.parts), self.messaging_type, self.tag,
            dict(self.metadata))


class DeliveryService:
    def __init__(self, client: MessengerClient, outbox: Outbox,
                 state: StateStore, coach_base_url: str,
                 notebook: NotebookService | None = None) -> None:
        self.client = client
        self.outbox = outbox
        self.state = state
        self.coach_base_url = coach_base_url
        self.notebook = notebook

    def stored(self, source_key: str) -> StoredDelivery | None:
        row = self.state.stored_outbound_delivery(source_key)
        if row is None:
            return None
        return StoredDelivery(source_key, row["psid"], row["status"], row["payload"])

    def prepare(self, envelope: OutboundEnvelope) -> bool:
        """Store a computed output without sending it; False if it already went out."""
        if self.notebook:
            self.notebook.capture(envelope)
        return self.state.prepare_outbound_delivery(
            envelope.source_key, envelope.psid, self._digest(envelope),
            envelope_payload(envelope))

    def discard(self, source_key: str) -> None:
        """Forget a prepared output that became obsolete (never a delivered one)."""
        self.state.discard_outbound_delivery(source_key)

    def redeliver(self, stored: StoredDelivery,
                  images: tuple[ImagePart, ...] = ()) -> DeliveryReceipt:
        """Resend the stored envelope: no computation, no model call."""
        envelope = envelope_from_payload(stored)
        if images:
            envelope = OutboundEnvelope(
                envelope.psid, envelope.source_key, envelope.parts + images,
                envelope.messaging_type, envelope.tag, envelope.meta)
        return self.deliver(envelope)

    def resume(self, stored: StoredDelivery) -> DeliveryReceipt:
        """Resume an already computed output where it stopped."""
        if stored.status in {DeliveryStatus.DELIVERED, DeliveryStatus.SENDING}:
            # Already accepted by Meta, or possibly so: never resent (at-most-once).
            return DeliveryReceipt(text_ok=True, attachments_ok=True, delivered=1,
                                   replayed=True, uncertain=stored.status == DeliveryStatus.SENDING)
        return self.redeliver(stored)

    def deliver(self, envelope: OutboundEnvelope) -> DeliveryReceipt:
        if self.notebook:
            self.notebook.capture(envelope)
        disposition = self.state.begin_outbound_delivery(
            envelope.source_key, envelope.psid, self._digest(envelope),
            envelope_payload(envelope))
        if disposition is not Disposition.SEND:
            # The Send API offers no idempotency key. After a crash in the middle of
            # the call, replaying would almost surely create a duplicate; we therefore
            # choose at-most-once and let `uncertain` trigger an internal trace.
            return DeliveryReceipt(
                text_ok=True, attachments_ok=True, delivered=1, replayed=True,
                uncertain=disposition is Disposition.UNCERTAIN)
        delivered = 0
        text_delivered = 0
        messages: list[DeliveredMessage] = []
        images: list[str] = []
        text_ok = True
        attachments_ok = True
        detail = ""

        for part in envelope.parts:
            # No orphan artifact: if the prose that gives it meaning fails, everything
            # left is archived without being sent.
            if isinstance(part, ImagePart) and not text_ok:
                self._discard_image(part.path)
                continue
            result = self._deliver_part(envelope, part)
            delivered += result.delivered
            if not isinstance(part, ImagePart):
                text_delivered += result.delivered
                messages.extend(result.messages)
            if result.ok:
                if isinstance(part, ImagePart):
                    images.append(part.path.name)
                continue
            detail = detail or result.detail
            if isinstance(part, ImagePart):
                attachments_ok = False
            else:
                text_ok = False
                # A text failure stops the following texts. They would form a
                # truncated reply in the wrong order; the images are discarded by
                # the branch above.
                break

        # If the loop stopped on a text, the unvisited images must still leave the
        # outbox root.
        if not text_ok:
            for part in envelope.parts:
                if isinstance(part, ImagePart) and part.path.exists():
                    self._discard_image(part.path)
        receipt = DeliveryReceipt(
            text_ok=text_ok, attachments_ok=attachments_ok, delivered=delivered,
            detail=detail, messages=tuple(messages))
        has_text = any(not isinstance(part, ImagePart) for part in envelope.parts)
        # `failed` must mean that no fragment crossed the transport.
        accepted = ((text_ok or text_delivered > 0) if has_text else delivered > 0)
        self.state.finish_outbound_delivery(
            envelope.source_key, delivered=accepted, error=detail)
        return receipt

    def _deliver_part(self, envelope: OutboundEnvelope,
                      part: OutboundPart) -> DeliveryResult:
        if isinstance(part, TextPart):
            if part.pause_before:
                self.client.pause_before(envelope.psid, part.text)
            if part.replies:
                return self.client.send_quick_replies(
                    envelope.psid, part.text, part.replies,
                    messaging_type=envelope.messaging_type, tag=envelope.tag)
            sender = self.client.send_burst if part.mode == "burst" else self.client.send_text
            return sender(
                envelope.psid, part.text, messaging_type=envelope.messaging_type,
                tag=envelope.tag)
        return self._deliver_image(envelope, part)

    def _deliver_image(self, envelope: OutboundEnvelope,
                       part: ImagePart) -> DeliveryResult:
        result = self.client.send_image(
            envelope.psid, str(part.path), messaging_type=envelope.messaging_type,
            tag=envelope.tag)
        if not result.ok:
            log.error("Image not delivered, quarantined: %s", part.path.name)
            self.outbox.fail(part.path)
            return result
        log.info("Image sent: %s", part.path.name)
        self.outbox.archive(part.path)
        return result

    def _discard_image(self, path: Path) -> None:
        log.info("Image discarded (no text output): %s", path.name)
        self.outbox.archive(path)

    @staticmethod
    def _digest(envelope: OutboundEnvelope) -> str:
        def payload(part: OutboundPart) -> dict:
            if isinstance(part, TextPart):
                # Replies count only when there are: a text without them keeps the digest
                # it always had.
                return {"kind": "text", "text": part.text, "mode": part.mode,
                        "pause_before": part.pause_before,
                        **({"replies": [list(reply) for reply in part.replies]}
                           if part.replies else {})}
            return {"kind": "image", "path": part.path.name, "link": part.link}

        canonical = json.dumps({
            "psid": envelope.psid, "messaging_type": envelope.messaging_type,
            "tag": envelope.tag, "parts": [payload(part) for part in envelope.parts],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
