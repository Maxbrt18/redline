"""A voice note becomes the athlete's text, or an answer that says it could not be heard."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from redline.coach.fr import transcription as wording
from redline.model.errors import ExternalError, InputError
from redline.service.attachments import AttachmentError, LocalAttachment, encode_payload
from redline.service.work_queue import (
    AttachmentRequest,
    DeepRequest,
    InboundRequest,
    VoiceRequest,
    WorkQueue,
)

PSID = "4242"
MESSAGE_ID = "mid-voice"
PAYLOAD = encode_payload([{"type": "audio", "payload": {
    "url": "https://cdn.fbsbx.com/audioclip-1.mp4"}}])


class FakeVoiceAttachments:
    def __init__(self, tmp_path, error=None):
        self.path = tmp_path / "inbound-voice.m4a"
        self.error = error

    def cleanup(self):
        return 0

    @contextmanager
    def materialize_voice(self, _payload, _message_id):
        if self.error is not None:
            raise self.error
        self.path.write_bytes(b"\x00\x00\x00\x18ftypM4A")
        try:
            yield (LocalAttachment("audio", self.path, "audio/mp4", 12),)
        finally:
            self.path.unlink(missing_ok=True)


class FakeTranscriber:
    provider = "fake"

    def __init__(self, text="", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def transcribe(self, clip, hints):
        self.calls.append((clip, hints))
        assert clip.path.exists(), "the clip is transcribed while it exists"
        if self.error is not None:
            raise self.error
        return self.text


@pytest.fixture
def voice_bot(make_bot, store, tmp_path):
    def build(transcriber=None, attachments_error=None):
        store.claim_inbound(MESSAGE_ID, PSID, "attachment", PAYLOAD)
        return make_bot(attachments=FakeVoiceAttachments(tmp_path, attachments_error),
                        transcriber=transcriber, debounce_seconds=0, debounce_max_seconds=0)
    return build


def queued_voice(bot):
    bot.submit_attachment(PSID, PAYLOAD, message_id=MESSAGE_ID)
    job = bot._queue.next()
    assert isinstance(job, VoiceRequest)
    return job


def test_a_transcript_becomes_a_durable_text_that_joins_the_conversation(voice_bot, store):
    transcriber = FakeTranscriber("J'ai fait mon seuil, RPE 7.")
    bot = voice_bot(transcriber)

    assert bot._transcribe_voice(queued_voice(bot)) is True

    inbound = bot._queue.next()
    assert isinstance(inbound, InboundRequest)
    assert inbound.message_ids == (MESSAGE_ID,)
    assert inbound.text == wording.MESSAGE.format(text="J'ai fait mon seuil, RPE 7.")
    [row] = store.pending_inbound()
    assert (row["kind"], row["body"], row["trace"]) == (
        "text", inbound.text, wording.TRACE.format(text="J'ai fait mon seuil, RPE 7."))
    _, hints = transcriber.calls[0]
    assert hints.language == "fr" and hints.vocabulary[0] == "Sam"


def test_without_a_transcriber_a_voice_note_takes_the_attachment_path(voice_bot):
    bot = voice_bot(transcriber=None)
    bot.submit_attachment(PSID, PAYLOAD, message_id=MESSAGE_ID)
    assert isinstance(bot._queue.next(), AttachmentRequest)


@pytest.mark.parametrize("transcriber, attachments_error, answer", [
    (FakeTranscriber(error=InputError("refused")), None, wording.UNREADABLE),
    (FakeTranscriber("   "), None, wording.SILENT),
    (FakeTranscriber("jamais lu"), AttachmentError("Ce fichier dépasse la limite de 12 Mo."),
     "Ce fichier dépasse la limite de 12 Mo."),
], ids=["refused", "silent", "download-refused"])
def test_a_voice_note_that_cannot_be_heard_is_answered_and_acknowledged(
        voice_bot, store, transcriber, attachments_error, answer):
    bot = voice_bot(transcriber, attachments_error)

    assert bot._transcribe_voice(queued_voice(bot)) is True

    assert store._conversation[-1]["text"] == answer
    assert store._conversation[-1]["source_key"] == f"attachment:{MESSAGE_ID}"
    assert store.pending_inbound() == []


def test_a_provider_outage_keeps_the_voice_note_pending_for_a_retry(voice_bot, store):
    bot = voice_bot(FakeTranscriber(error=ExternalError("HTTP 503")))

    assert bot._transcribe_voice(queued_voice(bot)) is False

    [row] = store.pending_inbound()
    assert row["kind"] == "attachment"
    assert store._conversation == []


def test_a_voice_note_is_transcribed_before_slower_work_and_counts_as_pending():
    queue = WorkQueue(debounce_seconds=5, debounce_max_seconds=20)
    queue.submit_attachment(PSID, PAYLOAD, "mid-photo")
    queue.submit_inbound(PSID, "/deep pourquoi ?", "mid-deep")
    queue.submit_voice(PSID, PAYLOAD, MESSAGE_ID)

    assert queue.has_pending_inbound()
    assert isinstance(queue.next(), VoiceRequest)
    assert isinstance(queue.next(), DeepRequest)
