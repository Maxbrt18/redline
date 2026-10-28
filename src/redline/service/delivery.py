"""Composition et livraison des sorties Messenger.

Le transport sait envoyer un texto ou une image. Ce module porte l'unité métier :
une sortie complète, ordonnée, avec un verdict distinct pour son texte et ses
artefacts. Le bot peut ainsi acquitter un travail à partir de ce qui a réellement
franchi le transport, sans recoder les succès partiels pour chaque déclencheur.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import strength
from .messenger import DeliveredMessage, DeliveryResult, MessengerClient
from .notebook import NotebookService
from .outbox import Outbox, OutboxSnapshot
from .state import StateStore

log = logging.getLogger("redline")


@dataclass(frozen=True)
class TextPart:
    text: str
    mode: Literal["text", "burst"] = "burst"
    pause_before: bool = False


@dataclass(frozen=True)
class ImagePart:
    path: Path
    link: tuple[str, dict] | None = None


@dataclass(frozen=True)
class NotebookButtonPart:
    """Un texte et un bouton vers le carnet. Le lien se frappe à l'envoi : un jeton
    de connexion ne se stocke ni ne se rejoue."""
    text: str


OutboundPart = TextPart | ImagePart | NotebookButtonPart


@dataclass(frozen=True)
class OutboundEnvelope:
    psid: str
    source_key: str
    parts: tuple[OutboundPart, ...]
    messaging_type: str = "RESPONSE"
    tag: str | None = None
    # Ce que le bot doit savoir pour acquitter l'envoi s'il est rejoué plus tard (tour,
    # texte à mémoriser, révision montrée). Hors empreinte : ce n'est pas du contenu.
    meta: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class StoredDelivery:
    """Une enveloppe déjà calculée, telle que PostgreSQL la conserve."""

    source_key: str
    psid: str
    status: str
    payload: dict | None

    @property
    def meta(self) -> dict:
        return dict((self.payload or {}).get("meta") or {})

    @property
    def resendable(self) -> bool:
        """Calculée, jamais acceptée par Meta : on peut la renvoyer telle quelle."""
        return self.status in {"prepared", "failed"} and bool(
            (self.payload or {}).get("parts"))


def envelope_payload(envelope: OutboundEnvelope) -> dict:
    """La forme durable d'une enveloppe.

    Le carnet conserve les images séparément avant l’envoi. La reprise de transport
    ne réexpédie que le texte ; les cartes restent accessibles dans le carnet.
    """
    parts: list[dict] = []
    for part in envelope.parts:
        if isinstance(part, TextPart):
            parts.append({"kind": "text", "text": part.text, "mode": part.mode,
                          "pause_before": part.pause_before})
        elif isinstance(part, NotebookButtonPart):
            parts.append({"kind": "notebook_button", "text": part.text})
    return {"messaging_type": envelope.messaging_type, "tag": envelope.tag,
            "parts": parts, "meta": dict(envelope.meta)}


def envelope_from_payload(stored: StoredDelivery) -> OutboundEnvelope:
    payload = stored.payload or {}
    parts: list[OutboundPart] = []
    for item in payload.get("parts", ()):
        if item.get("kind") == "text":
            parts.append(TextPart(str(item["text"]), item.get("mode", "burst"),
                                  bool(item.get("pause_before"))))
        elif item.get("kind") == "notebook_button":
            parts.append(NotebookButtonPart(str(item["text"])))
    if not parts:
        raise ValueError(f"enveloppe stockée sans contenu : {stored.source_key}")
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
    """Petit assembleur mutable ; l'enveloppe produite reste, elle, immuable."""

    psid: str
    source_key: str
    messaging_type: str = "RESPONSE"
    tag: str | None = None
    parts: list[OutboundPart] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def meta(self, **values: Any) -> EnvelopeBuilder:
        self.metadata.update(values)
        return self

    def text(self, value: str, *, burst: bool = True,
             pause_before: bool = False) -> EnvelopeBuilder:
        if value.strip():
            self.parts.append(TextPart(value, "burst" if burst else "text", pause_before))
        return self

    def notebook_button(self, text: str) -> EnvelopeBuilder:
        self.parts.append(NotebookButtonPart(text))
        return self

    def images(self, outbox: Outbox, before: OutboxSnapshot) -> EnvelopeBuilder:
        for path in outbox.collect_new(before):
            self.parts.append(ImagePart(path, outbox.link_for(path)))
        return self

    def image(self, outbox: Outbox, path: Path) -> EnvelopeBuilder:
        """Ajoute l'artefact explicitement produit par un renderer typé."""
        self.parts.append(ImagePart(path, outbox.link_for(path)))
        return self

    def build(self) -> OutboundEnvelope:
        if not self.parts:
            raise ValueError("une enveloppe de sortie ne peut pas être vide")
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
        """Conserve une sortie calculée sans l'envoyer ; False si elle est déjà partie."""
        if self.notebook:
            self.notebook.capture(envelope)
        return self.state.prepare_outbound_delivery(
            envelope.source_key, envelope.psid, self._digest(envelope),
            envelope_payload(envelope))

    def discard(self, source_key: str) -> None:
        """Oublie une sortie préparée devenue caduque (jamais une sortie livrée)."""
        self.state.discard_outbound_delivery(source_key)

    def redeliver(self, stored: StoredDelivery,
                  images: tuple[ImagePart, ...] = ()) -> DeliveryReceipt:
        """Renvoie l'enveloppe conservée : aucun calcul, aucun appel de modèle."""
        envelope = envelope_from_payload(stored)
        if images:
            envelope = OutboundEnvelope(
                envelope.psid, envelope.source_key, envelope.parts + images,
                envelope.messaging_type, envelope.tag, envelope.meta)
        return self.deliver(envelope)

    def resume(self, stored: StoredDelivery) -> DeliveryReceipt:
        """Reprend une sortie déjà calculée là où elle s'est arrêtée."""
        if stored.status in {"delivered", "sending"}:
            # Déjà acceptée par Meta, ou peut-être : jamais renvoyée (at-most-once).
            return DeliveryReceipt(text_ok=True, attachments_ok=True, delivered=1,
                                   replayed=True, uncertain=stored.status == "sending")
        return self.redeliver(stored)

    def deliver(self, envelope: OutboundEnvelope) -> DeliveryReceipt:
        entries = self.notebook.capture(envelope) if self.notebook else []
        disposition = self.state.begin_outbound_delivery(
            envelope.source_key, envelope.psid, self._digest(envelope),
            envelope_payload(envelope))
        if disposition in {"delivered", "uncertain"}:
            # La Send API n'offre pas de clé d'idempotence. Après un crash au milieu
            # de l'appel, rejouer créerait sûrement un doublon ; on choisit donc
            # l'at-most-once et on laisse `uncertain` déclencher une trace interne.
            return DeliveryReceipt(
                text_ok=True, attachments_ok=True, delivered=1, replayed=True,
                uncertain=disposition == "uncertain")
        delivered = 0
        text_delivered = 0
        messages: list[DeliveredMessage] = []
        images: list[str] = []
        text_ok = True
        attachments_ok = True
        detail = ""

        for part in envelope.parts:
            # Aucun artefact orphelin : si la prose qui lui donne son sens échoue,
            # tout ce qui reste est archivé sans être expédié.
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
                # Un échec de texte arrête les textes suivants. Ils formeraient une
                # réponse tronquée dans le mauvais ordre ; les images seront jetées
                # par la branche ci-dessus.
                break

        # Si la boucle s'est arrêtée sur le texte, les images non visitées doivent
        # tout de même quitter la racine de l'outbox.
        if not text_ok:
            for part in envelope.parts:
                if isinstance(part, ImagePart) and part.path.exists():
                    self._discard_image(part.path)
        receipt = DeliveryReceipt(
            text_ok=text_ok, attachments_ok=attachments_ok, delivered=delivered,
            detail=detail, messages=tuple(messages))
        has_text = any(not isinstance(part, ImagePart) for part in envelope.parts)
        # Une enveloppe sans texte peut quand même avoir expédié sa carte avant
        # l'échec du bouton de séance. Elle devient alors non rejouable elle aussi :
        # `failed` doit signifier qu'aucun fragment n'a franchi le transport.
        accepted = ((text_ok or text_delivered > 0) if has_text else delivered > 0)
        self.state.finish_outbound_delivery(
            envelope.source_key, delivered=accepted, error=detail)
        if entries and text_ok and self.notebook:
            try:
                button = self.client.send_button(
                    envelope.psid, "Tes cartes et bilans restent dans ton carnet.",
                    self.notebook.link(envelope.psid, entries[0]), "Ouvrir mon carnet",
                    messaging_type=envelope.messaging_type, tag=envelope.tag)
                if not button.ok:
                    log.warning("Bouton du carnet non livré ; archive conservée")
            except Exception:
                log.exception("Lien du carnet indisponible ; archive conservée")
        return receipt

    def _deliver_part(self, envelope: OutboundEnvelope,
                      part: OutboundPart) -> DeliveryResult:
        if isinstance(part, TextPart):
            if part.pause_before:
                self.client.pause_before(envelope.psid, part.text)
            sender = self.client.send_burst if part.mode == "burst" else self.client.send_text
            return sender(
                envelope.psid, part.text, messaging_type=envelope.messaging_type,
                tag=envelope.tag)
        if isinstance(part, NotebookButtonPart):
            return self._deliver_notebook_button(envelope, part)
        return self._deliver_image(envelope, part)

    def _deliver_notebook_button(self, envelope: OutboundEnvelope,
                                 part: NotebookButtonPart) -> DeliveryResult:
        url = None
        if self.notebook:
            try:
                url = self.notebook.link(envelope.psid)
            except Exception:
                log.exception("Lien du carnet indisponible ; texte seul")
        if url is None:
            return self.client.send_text(
                envelope.psid, part.text, messaging_type=envelope.messaging_type,
                tag=envelope.tag)
        return self.client.send_button(
            envelope.psid, part.text, url, "Ouvrir mon carnet",
            messaging_type=envelope.messaging_type, tag=envelope.tag)

    def _deliver_image(self, envelope: OutboundEnvelope,
                       part: ImagePart) -> DeliveryResult:
        result = self.client.send_image(
            envelope.psid, str(part.path), messaging_type=envelope.messaging_type,
            tag=envelope.tag)
        if not result.ok:
            log.error("Image non livrée, mise en quarantaine : %s", part.path.name)
            self.outbox.fail(part.path)
            return result
        log.info("Image envoyée : %s", part.path.name)
        if not part.link:
            self.outbox.archive(part.path)
            return result

        text, session = part.link
        token = ""
        try:
            token = self.state.issue_strength_link(envelope.psid, session)
            url = strength.token_url(self.coach_base_url, token)
        except Exception as exc:
            log.exception("Lien de séance non créé : %s", part.path.name)
            self.outbox.archive(part.path)
            return DeliveryResult.failure(
                f"lien de séance non créé : {exc}", delivered=result.delivered,
                messages=result.messages)
        button = self.client.send_button(
            envelope.psid, text, url, strength.BUTTON_TITLE,
            messaging_type=envelope.messaging_type, tag=envelope.tag)
        self.outbox.archive(part.path)
        if not button.ok:
            log.error("Bouton de séance non livré : %s", part.path.name)
            self.state.revoke_strength_link(envelope.psid, token)
            return DeliveryResult.failure(
                button.detail, delivered=result.delivered + button.delivered,
                messages=result.messages + button.messages)
        log.info("Lien de séance envoyé : %s", part.path.name)
        return DeliveryResult.success(
            result.delivered + button.delivered, result.messages + button.messages)

    def _discard_image(self, path: Path) -> None:
        log.info("Image écartée (sortie textuelle absente) : %s", path.name)
        self.outbox.archive(path)

    @staticmethod
    def _digest(envelope: OutboundEnvelope) -> str:
        def payload(part: OutboundPart) -> dict:
            if isinstance(part, TextPart):
                return {"kind": "text", "text": part.text, "mode": part.mode,
                        "pause_before": part.pause_before}
            if isinstance(part, NotebookButtonPart):
                return {"kind": "notebook_button", "text": part.text}
            return {"kind": "image", "path": part.path.name, "link": part.link}

        canonical = json.dumps({
            "psid": envelope.psid, "messaging_type": envelope.messaging_type,
            "tag": envelope.tag, "parts": [payload(part) for part in envelope.parts],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
