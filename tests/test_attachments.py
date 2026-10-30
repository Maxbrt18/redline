"""The attachment is readable for a few seconds, never a new fact base."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from redline.service.agent import RunResult
from redline.service.attachments import (
    AttachmentError,
    AttachmentService,
    LocalAttachment,
    MaterializedAttachments,
    encode_payload,
    safe_conversation_label,
)
from redline.service.messenger import DeliveredMessage, DeliveryResult
from redline.service.work_queue import AttachmentRequest


class Response:
    def __init__(self, body: bytes, media_type: str, *, status=200, location=""):
        self.body = body
        self.status_code = status
        self.headers = {"Content-Type": media_type,
                        "Content-Length": str(len(body))}
        if location:
            self.headers["Location"] = location
        self.closed = False

    def iter_content(self, _size):
        yield self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self.responses.pop(0)


def payload(kind="image", url="https://lookaside.fbsbx.com/a.jpg"):
    return encode_payload([{"type": kind, "payload": {"url": url}}], "ma légende")


def test_a_photo_exists_only_inside_the_context_manager(tmp_path):
    response = Response(b"\xff\xd8\xffphoto", "image/jpeg")
    service = AttachmentService(
        tmp_path, max_bytes=1024, total_bytes=2048, session=Session(response))

    with service.materialize(payload(), "mid-1") as batch:
        path = batch.files[0].path
        assert path.exists()
        assert path.stat().st_mode & 0o777 == 0o600
        assert service.caption(payload()) == "ma légende"

    assert not path.exists()


def test_video_is_explicitly_refused_without_any_download(tmp_path):
    session = Session()
    service = AttachmentService(tmp_path, max_bytes=1024, total_bytes=2048,
                                session=session)
    with pytest.raises(AttachmentError, match="vidéos"), service.materialize(
            payload("video", "https://lookaside.fbsbx.com/v.mp4"), "mid-video"):
        pass
    assert session.urls == []


def test_a_non_meta_host_is_refused_before_network(tmp_path):
    session = Session()
    service = AttachmentService(tmp_path, max_bytes=1024, total_bytes=2048,
                                session=session)
    with pytest.raises(AttachmentError, match="URL Meta sûre"), service.materialize(
            payload(url="https://example.com/a.jpg"), "mid-bad"):
        pass
    assert session.urls == []


def test_content_is_sniffed_in_addition_to_the_http_label(tmp_path):
    service = AttachmentService(
        tmp_path, max_bytes=1024, total_bytes=2048,
        session=Session(Response(b"<script>non</script>", "image/jpeg")))
    with pytest.raises(AttachmentError, match="contenu réel"), service.materialize(
            payload(), "mid-fake"):
        pass
    assert list(tmp_path.iterdir()) == []


def test_redirect_cannot_leave_meta_domains(tmp_path):
    first = Response(b"", "text/plain", status=302,
                     location="http://127.0.0.1/private")
    service = AttachmentService(
        tmp_path, max_bytes=1024, total_bytes=2048, session=Session(first))
    with pytest.raises(AttachmentError, match="redirection"), service.materialize(
            payload(), "mid-redirect"):
        pass


def test_relation_memory_never_keeps_the_url_or_filename():
    raw = encode_payload([{"type": "file", "payload": {
        "url": "https://lookaside.fbsbx.com/secret.pdf", "name": "medical.pdf"}}])
    label = safe_conversation_label(raw)
    assert label == "[Pièce jointe : document]"
    assert "secret" not in label and "medical" not in label


class FakeAttachments:
    def __init__(self, tmp_path):
        self.path = tmp_path / "inbound-test.jpg"

    def cleanup(self):
        return 0

    @contextmanager
    def materialize(self, _payload, _message_id):
        self.path.write_bytes(b"\xff\xd8\xffphoto")
        try:
            yield MaterializedAttachments((LocalAttachment(
                "image", self.path, "image/jpeg", 8),))
        finally:
            self.path.unlink(missing_ok=True)

    def caption(self, _payload):
        return "semelle après 500 km"


def test_bot_analysis_is_read_only_and_drops_any_staged_write(
        make_bot, tmp_path, store):
    from conftest import FakeAgent

    agent = FakeAgent(result=RunResult(full_text=(
        "Observable : usure latérale. Candidat à enregistrer : contrôle de la paire. "
        "Tu confirmes ?")))
    attachments = FakeAttachments(tmp_path)
    delivered = DeliveryResult.success(
        messages=(DeliveredMessage("out-1", "analyse"),))
    client = SimpleNamespace(
        action=lambda *_, **__: DeliveryResult.success(),
        send_burst=lambda *_, **__: delivered,
        send_text=lambda *_, **__: delivered,
        send_image=lambda *_, **__: DeliveryResult.success(),
        send_button=lambda *_, **__: DeliveryResult.success(),
        pause_before=lambda *_, **__: None,
    )
    bot = make_bot(agent=agent, client=client, attachments=attachments)
    bot._respond_attachment(AttachmentRequest("4242", payload(), "mid-photo"))

    assert agent.calls[0]["read_only"] is True
    assert "TOUR MÉCANIQUEMENT EN LECTURE SEULE" in agent.calls[0]["message"]
    assert "confirmation" in agent.calls[0]["message"]
    bot._repo.discard_turn.assert_called_once_with(agent.calls[0]["turn_id"])
    assert not attachments.path.exists()
    assert store._conversation[-1]["source_key"] == "attachment:mid-photo"


def test_completed_attachment_erases_its_transport_payload(store):
    raw = payload()
    store.claim_inbound("mid", "4242", "attachment", raw)
    store.complete_inbound(("mid",))
    assert store._inbound_messages["mid"]["body"] == ""
