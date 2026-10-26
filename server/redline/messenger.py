"""Messenger Send API transport with explicit delivery results."""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import requests

from .config import Config
from .prompts import DEEP_PATIENCE_LINES, PATIENCE_LINES
from .textutils import burst_parts, strip_links

log = logging.getLogger("redline")


@dataclass(frozen=True)
class DeliveredMessage:
    """Un texto réellement accepté par Meta, avec l'identifiant qui permettra
    ensuite de reconnaître un swipe-reply vers CE texto précis."""

    message_id: str
    text: str


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    delivered: int = 0
    detail: str = ""
    messages: tuple[DeliveredMessage, ...] = ()

    @classmethod
    def success(cls, delivered: int = 1,
                messages: tuple[DeliveredMessage, ...] = ()) -> DeliveryResult:
        return cls(True, delivered, "", messages)

    @classmethod
    def failure(cls, detail: str, delivered: int = 0,
                messages: tuple[DeliveredMessage, ...] = ()) -> DeliveryResult:
        return cls(False, delivered, detail, messages)


class MessengerClient:
    MAX_LEN = 1900  # Messenger caps a text at 2000 characters: leave a margin

    def __init__(self, config: Config) -> None:
        self._url = f"https://graph.facebook.com/{config.graph_version}/me/messages"
        self._headers = {"Authorization": f"Bearer {config.page_token}"}

    def action(self, psid: str, action: str) -> DeliveryResult:
        """Conversation indicator: "mark_seen" or "typing_on"."""
        return self._post({"recipient": {"id": psid}, "sender_action": action})

    def send_text(self, psid: str, text: str,
                  messaging_type: str = "RESPONSE", tag: str | None = None) -> DeliveryResult:
        text = strip_links(text).strip()
        if not text:
            return DeliveryResult.success(0)
        delivered = 0
        messages: list[DeliveredMessage] = []
        for chunk in self._split(text):
            message = {
                "recipient": {"id": psid},
                "messaging_type": messaging_type,
                "message": {"text": chunk},
            }
            if tag:
                message["tag"] = tag
            result = self._post(message)
            delivered += result.delivered
            messages.extend(result.messages)
            if not result.ok:
                return DeliveryResult.failure(result.detail, delivered, tuple(messages))
        return DeliveryResult.success(delivered, tuple(messages))

    PAUSE_MAX = 2.5          # nobody waits eight seconds between two texts
    PAUSE_BASE = 0.6
    PAUSE_PER_CHAR = 0.01

    def pause_before(self, psid: str, part: str) -> None:
        """The human rhythm between two texts of a burst: the typing bubble, then a
        pause that grows with what is about to arrive.

        Shared with bot.py, which paces the STREAMED reply the same way: two
        codepaths sending the same burst must not drift into two rhythms.
        """
        self.action(psid, "typing_on")
        time.sleep(min(self.PAUSE_MAX, self.PAUSE_BASE + self.PAUSE_PER_CHAR * len(part)))

    def send_burst(self, psid: str, text: str,
                   messaging_type: str = "RESPONSE", tag: str | None = None) -> DeliveryResult:
        delivered = 0
        messages: list[DeliveredMessage] = []
        for index, part in enumerate(self._burst_parts(text)):
            if index:
                self.pause_before(psid, part)
            result = self.send_text(psid, part, messaging_type=messaging_type, tag=tag)
            delivered += result.delivered
            messages.extend(result.messages)
            if not result.ok:
                return DeliveryResult.failure(result.detail, delivered, tuple(messages))
        return DeliveryResult.success(delivered, tuple(messages))

    IMAGE_TYPES: ClassVar[dict] = {".png": "image/png", ".jpg": "image/jpeg",
                                   ".jpeg": "image/jpeg"}

    def send_image(self, psid: str, path: str,
                   messaging_type: str = "RESPONSE", tag: str | None = None) -> DeliveryResult:
        """Send an image as an attachment (multipart upload of the Send API).
        Same philosophy as send_text: retries, never raises."""
        file = Path(path)
        mime = self.IMAGE_TYPES.get(file.suffix.lower())
        if mime is None or not file.is_file():
            log.error("Image skipped (missing or unknown format): %s", path)
            return DeliveryResult.failure(f"image invalide : {path}")
        data = {
            "recipient": json.dumps({"id": psid}),
            "messaging_type": messaging_type,
            "message": json.dumps({"attachment": {"type": "image",
                                                  "payload": {"is_reusable": False}}}),
        }
        if tag:
            data["tag"] = tag
        def upload():
            # The file is reopened on each attempt: the stream is consumed by the upload.
            with file.open("rb") as handle:
                return requests.post(self._url, headers=self._headers, data=data,
                                     files={"filedata": (file.name, handle, mime)},
                                     timeout=self.UPLOAD_TIMEOUT)

        return self._with_retry(upload, "Image upload", (OSError, requests.RequestException))

    BUTTON_TEXT_MAX = 640    # Messenger caps a button template's text
    BUTTON_TITLE_MAX = 20    # ...and its button label, hard

    def send_button(self, psid: str, text: str, url: str, title: str,
                    messaging_type: str = "RESPONSE", tag: str | None = None) -> DeliveryResult:
        """A one-button template pointing at a web page.

        Used for the interactive strength session, right after its card. Why a
        template and not a URL in the prose: the STYLE_PROMPT forbids the coach
        from writing links, strip_links() eats markdown ones, and no one should
        have to retype 1000 characters of base64 without a typo. Here the link is
        a control, and the coach's text stays a coach's text.
        """
        message = {
            "recipient": {"id": psid},
            "messaging_type": messaging_type,
            "message": {"attachment": {"type": "template", "payload": {
                "template_type": "button",
                "text": (text or "Ta séance")[:self.BUTTON_TEXT_MAX],
                "buttons": [{"type": "web_url", "url": url,
                             "title": title[:self.BUTTON_TITLE_MAX]}],
            }}},
        }
        if tag:
            message["tag"] = tag
        return self._post(message)

    ATTEMPTS = 3
    # (connexion, lecture). Meta répond en une seconde : attendre une minute une
    # réponse perdue retardait chaque fragment de trois minutes au pire.
    TIMEOUT = (5, 10)
    UPLOAD_TIMEOUT = (5, 60)

    def _with_retry(self, send, label: str, catch=requests.RequestException,
                    attempts: int = 0) -> DeliveryResult:
        """Retry on network errors, saturation and transient HTTP failures.

        `send_image` used to hold a second copy of this loop, with the 3 spelled out
        three times in its own log strings: changing the retry policy meant editing
        two places and not forgetting the messages (2026-07-29).
        """
        attempts = attempts or self.ATTEMPTS
        for attempt in range(1, attempts + 1):
            response = None
            try:
                response = send()
            except requests.ReadTimeout as exc:
                # La requête a pu être acceptée : la renvoyer risquerait un doublon.
                # La Send API n'a pas d'idempotence ; le doute vaut livraison.
                log.error("%s sans réponse après envoi : livraison incertaine (%s)",
                          label, exc)
                return DeliveryResult.failure(
                    f"{label} : livraison incertaine, délai de lecture dépassé",
                    delivered=1)
            except catch as exc:
                log.warning("%s unreachable (attempt %d/%d): %s",
                            label, attempt, attempts, exc)
            else:
                retryable_status = response.status_code in {408, 425, 429} \
                    or response.status_code >= 500
                if not retryable_status:
                    if response.status_code >= 400:
                        log.error("%s %d: %s", label, response.status_code,
                                  response.text[:500])
                    if response.status_code >= 400:
                        return DeliveryResult.failure(
                            f"{label} {response.status_code}: {response.text[:200]}")
                    message_id = ""
                    try:
                        body = response.json()
                    except (AttributeError, TypeError, ValueError):
                        try:
                            body = json.loads(response.text)
                        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                            body = {}
                    if isinstance(body, dict):
                        message_id = str(body.get("message_id") or "")
                    messages = ((DeliveredMessage(message_id, ""),) if message_id else ())
                    return DeliveryResult.success(messages=messages)
                log.warning("%s %d transitoire (tentative %d/%d)",
                            label, response.status_code, attempt, attempts)
            if attempt < attempts:
                retry_after = 0.0
                if response is not None:
                    try:
                        retry_after = float(response.headers.get("Retry-After", 0) or 0)
                    except (AttributeError, TypeError, ValueError):
                        retry_after = 0.0
                time.sleep(max(2 * attempt, min(retry_after, 30.0)))
        return DeliveryResult.failure(f"{label} indisponible après {attempts} tentatives")

    def _post(self, payload: dict, attempts: int = 0) -> DeliveryResult:
        result = self._with_retry(
            lambda: requests.post(self._url, headers=self._headers,
                                  json=payload, timeout=self.TIMEOUT),
            "Send API", attempts=attempts)
        if not result.messages:
            return result
        message = payload.get("message") or {}
        text = str(message.get("text") or "")
        if not text:
            text = str(((message.get("attachment") or {}).get("payload") or {})
                        .get("text") or "")
        return DeliveryResult(
            result.ok, result.delivered, result.detail,
            tuple(DeliveredMessage(item.message_id, text) for item in result.messages))

    @classmethod
    def _burst_parts(cls, text: str) -> list:
        """Kept as the transport's name for the shared rule (`textutils.burst_parts`),
        which both codepaths must apply identically."""
        return burst_parts(text)

    @classmethod
    def _split(cls, text: str, limit: int = MAX_LEN) -> list:
        """Split an overlong text at a sentence end, never inside a word."""
        parts = []
        while len(text) > limit:
            cut = max(text.rfind(". ", 0, limit), text.rfind("! ", 0, limit),
                      text.rfind("? ", 0, limit), text.rfind("\n", 0, limit))
            if cut < limit // 2:
                cut = text.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit - 1
            parts.append(text[:cut + 1].strip())
            text = text[cut + 1:].strip()
        if text:
            parts.append(text)
        return parts


_last_static_line = ""
_last_line_lock = threading.Lock()


def _pick_static(lines: list) -> str:
    """A canned line, never the same one twice running.

    `random.choice` over four items repeats one time in four, and a repeated canned
    line is the most robotic thing the coach does.
    """
    global _last_static_line
    with _last_line_lock:
        candidates = [line for line in lines if line != _last_static_line] or list(lines)
        _last_static_line = random.choice(candidates)
        return _last_static_line


class TypingIndicator:
    """Keeps the "typing..." bubble alive throughout the agent's thinking.

    Meta turns it off after ~20 s: we refresh it periodically, and past
    PATIENCE_AFTER seconds we send a single waiting message -- the contextual one the
    triage wrote if there is one, a canned one otherwise. The bubble starts BEFORE the
    triage: `configure` then gives it the rung and the holding line once known.
    """

    REFRESH_EVERY = 15
    PATIENCE_AFTER = 75
    # La profondeur de lecture ne justifie pas une notification précoce.
    # Un seul message d'attente, soumis au même garde-fou de fréquence.
    BACKGROUND_AFTER = 90

    def __init__(self, client: MessengerClient, psid: str, patience: bool = True,
                 deep: bool = False, holding: str | None = None,
                 gate: Callable[[], bool] | None = None,
                 deliver: Callable[[str], object] | None = None) -> None:
        self._client = client
        self._psid = psid
        self._patience = patience
        self._deep = deep
        # Written by the triage from the message itself: it proves the coach read the
        # question. Absent (triage off, unparseable, too long) -> canned fallback.
        self._holding = holding
        # Asked ONCE, just before sending, and only if we are actually going to send:
        # it consumes the "no two waiting lines in a row" allowance. A coach who says
        # "two secs" every single time has a tic, not a rhythm.
        self._gate = gate
        # La phrase d'attente est un vrai message du coach : le bot la fait passer par
        # la livraison durable et la mémoire, pour qu'une réponse ciblée dessus
        # retrouve son texte.
        self._deliver = deliver or (lambda text: self._client.send_text(self._psid, text))
        self._stop = threading.Event()

    def configure(self, *, deep: bool, holding: str | None) -> None:
        """Le rang et la phrase d'attente, connus seulement après le triage."""
        self._deep = deep
        self._holding = holding

    def __enter__(self) -> TypingIndicator:
        self._client.action(self._psid, "typing_on")
        threading.Thread(target=self._refresh_loop, daemon=True, name="typing").start()
        return self

    def __exit__(self, *_exc_info) -> None:
        self._stop.set()

    def _patience_line(self) -> str:
        if self._holding:
            return self._holding
        return _pick_static(DEEP_PATIENCE_LINES if self._deep else PATIENCE_LINES)

    def _refresh_loop(self) -> None:
        started = time.monotonic()
        patience_sent = False
        after = self.PATIENCE_AFTER
        while not self._stop.is_set():
            elapsed = time.monotonic() - started
            if (self._patience and not patience_sent
                    and elapsed > after):
                patience_sent = True   # set first: a refused gate must not re-ask
                if self._gate is None or self._gate():
                    try:
                        self._deliver(self._patience_line())
                    except Exception:
                        log.exception("Phrase d'attente non livrée")
            if self._deep and elapsed > self.BACKGROUND_AFTER:
                # La requête continue ; seule l'animation cesse. Aucun deuxième texto,
                # même si le garde-fou a refusé le premier. /status et /cancel restent disponibles.
                return
            if self._stop.wait(self.REFRESH_EVERY):
                return
            self._client.action(self._psid, "typing_on")
