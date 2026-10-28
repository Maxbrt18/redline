"""Webhook facade: Messenger event extraction and Meta signature."""

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from conftest import ENGINE_ROOT, RecordingBot

# redline.web imports flask, which is a VPS runtime dependency (requirements.txt), not
# something a vault checkout on the Mac necessarily has. Without this, collecting this
# one module aborts the WHOLE run -- so `pytest` at the weekly review reported an error
# instead of the 116 tests that do pass.
pytest.importorskip("flask", reason="dépendance runtime du VPS (server/requirements.txt)")

from redline import clock, strength
from redline.web import (
    _iter_events,
    _signature_valid,
    create_app,
)
from scripts.model.dose import Dose


def wrap(*events) -> dict:
    return {"object": "page", "entry": [{"messaging": list(events)}]}


def test_text_message():
    payload = wrap({"sender": {"id": 4242}, "message": {"mid": "m1", "text": "salut coach"}})
    assert list(_iter_events(payload)) == [("4242", "m1", "text", "salut coach", "")]


def test_a_swipe_reply_keeps_the_exact_message_relation():
    payload = wrap({"sender": {"id": 4242}, "message": {
        "mid": "m-reply", "text": "Oui, c'est exactement ça.",
        "reply_to": {"mid": "m-old-coach"}}})
    assert list(_iter_events(payload)) == [(
        "4242", "m-reply", "text", "Oui, c'est exactement ça.", "m-old-coach")]


def test_sticker_distinguished_from_attachment():
    sticker = wrap({"sender": {"id": 1}, "message": {
        "mid": "m2", "attachments": [{"type": "image", "payload": {"sticker_id": 369}}]}})
    photo = wrap({"sender": {"id": 1}, "message": {
        "mid": "m3", "attachments": [{"type": "image", "payload": {"url": "https://cdn/x.jpg"}}]}})
    assert next(iter(_iter_events(sticker)))[2] == "sticker"
    assert next(iter(_iter_events(photo)))[2] == "attachment"


def test_attachment_payload_keeps_only_the_fields_needed_by_the_worker():
    payload = wrap({"sender": {"id": 1}, "message": {
        "mid": "m-photo", "text": "voici la semelle",
        "attachments": [{"type": "image", "payload": {
            "url": "https://lookaside.fbsbx.com/x.jpg", "name": "shoe.jpg",
            "irrelevant": "drop-me"}}]}})
    event = next(iter(_iter_events(payload)))
    assert event[:3] == ("1", "m-photo", "attachment")
    durable = json.loads(event[3])
    assert durable == {"version": 1, "caption": "voici la semelle", "attachments": [{
        "type": "image", "url": "https://lookaside.fbsbx.com/x.jpg"}]}


def test_postback_carries_its_sentence_in_payload():
    payload = wrap({"sender": {"id": 7}, "timestamp": 1752570000,
                    "postback": {"title": "Le programme du jour",
                                 "payload": "C'est quoi le programme du jour ?"}})
    events = list(_iter_events(payload))
    assert events == [(
        "7", "pb:1752570000", "postback", "C'est quoi le programme du jour ?", "")]


def test_quick_reply_keeps_its_machine_payload():
    payload = wrap({"sender": {"id": 7}, "message": {
        "mid": "m-quick", "text": "Légère",
        "quick_reply": {"payload": "CHOIX|legere"}}})
    assert list(_iter_events(payload)) == [(
        "7", "m-quick", "postback", "CHOIX|legere", "")]


def test_echo_and_unknown_objects_ignored():
    echo = wrap({"sender": {"id": 1}, "message": {"is_echo": True, "text": "..."}})
    assert list(_iter_events(echo)) == []
    assert list(_iter_events({"object": "instagram", "entry": []})) == []


def test_event_without_sender_ignored():
    payload = wrap({"message": {"mid": "m4", "text": "orphan"}})
    assert list(_iter_events(payload)) == []


def test_valid_signature():
    secret, body = "s3cret", b'{"hello": "world"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert _signature_valid(secret, body, f"sha256={digest}")
    assert not _signature_valid(secret, body, "sha256=deadbeef")
    assert not _signature_valid(secret, body, "md5=whatever")
    assert not _signature_valid("", body, "")


# --------------------------------------------------------- interactive session

VAULT = ENGINE_ROOT
SESSION = {
    "sid": "2026-07-25-A", "session": "a", "title": "Renfo A, os & injury",
    "date": "samedi 25 juillet",
    "exercises": [{"slug": "calf-raise-bent", "name": "Mollet unipodal",
                   "dose": Dose.read({"measure": "amrap", "sets": 2,
                                      "side": "each"}).payload()}],
}


@pytest.fixture
def app_bot(make_config, store, tmp_path):
    """The vault defaults to a THROWAWAY directory.

    It used to be the real one, which was harmless while the report route only
    forwarded a message -- and stopped being harmless the day it started writing
    `athlete/strength-log.csv` (2026-07-31): the suite would have appended rows to
    Sam's own log on every run. The two page tests below still ask for the real
    vault, because the page borrows its skin from `scripts/cards/`.
    """
    class RecordingOperationalStore:
        def __init__(self):
            self.sessions: dict[str, dict] = {}

        def accept_strength_report(self, payload, max_per_day):
            key = payload["session_key"]
            if key in self.sessions:
                return "duplicate"
            if len(self.sessions) >= max_per_day:
                return "quota"
            self.sessions[key] = payload
            return "accepted"

        def previous_strength_prescription(self, session, today):
            return None

        def pain_zones(self):
            return [{"slug": "injury", "label": "Injury", "active": True}]

    def factory(**overrides):
        config = make_config(**{"vault_path": str(tmp_path), **overrides})
        bot = RecordingBot()
        operational = RecordingOperationalStore()
        bot.operational_store = operational
        client = create_app(config, bot, store, operational).test_client()
        return client, bot, Path(config.vault_path)
    return factory


def post(client, body):
    return client.post("/strength-report", data=json.dumps(body),
                       content_type="application/json")


def token_body(store, report=None, session=None):
    token = store.issue_strength_link("4242", session or SESSION)
    return token, {"token": token,
                   "report": {"performed_on": clock.today().isoformat(),
                              **(report or {})}}


def test_session_page_carries_no_session_data(app_bot):
    client, _, _ = app_bot(vault_path=str(VAULT))
    response = client.get("/s")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # The page is a shell identical for everyone. A fragment capability is resolved
    # separately and therefore never appears in this request or in an access log.
    assert "Redline" in body
    assert "Mollet unipodal" not in body and SESSION["sid"] not in body
    assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_session_page_is_conditional(app_bot):
    client, _, _ = app_bot(vault_path=str(VAULT))
    etag = client.get("/s").headers["ETag"]
    assert client.get("/s", headers={"If-None-Match": etag}).status_code == 304


def test_report_reaches_the_athletes_own_queue(app_bot, store):
    client, bot, _ = app_bot()
    _, body = token_body(store, report={"pains": {"injury": {"after": 1}}, "minutes": 30})
    assert post(client, body).status_code == 201
    psid, text = bot.inbound[0]
    assert psid == "4242"
    assert "Renfo A, os & injury" in text and "Injury : 1/10 après" in text


def test_opaque_capability_resolves_the_exact_session(app_bot, store):
    client, _, _ = app_bot()
    token = store.issue_strength_link("4242", SESSION)
    response = client.post("/strength-session", json={"token": token})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    resolved = response.get_json()["session"]
    assert resolved["sid"] == SESSION["sid"]
    assert resolved["exercises"] == SESSION["exercises"]
    assert resolved["expires_at"] > int(time.time())


def test_unknown_or_revoked_capability_never_leaks_a_session(app_bot, store):
    client, _, _ = app_bot()
    unknown = strength.new_token()
    assert client.post("/strength-session", json={"token": unknown}).status_code == 403

    token = store.issue_strength_link("4242", SESSION)
    assert store.revoke_strength_link("4242", token)
    response = client.post("/strength-session", json={"token": token})
    assert response.status_code == 410
    assert response.get_json() == {"status": "expired"}


def test_session_resolution_contract_and_body_are_strict(app_bot, store):
    client, _, _ = app_bot()
    token = store.issue_strength_link("4242", SESSION)
    assert client.post("/strength-session", json={"token": token, "extra": 1}).status_code == 400
    assert client.post("/strength-session", data="pas du json").status_code == 400
    response = client.post("/strength-session", data=b"x" * 1025,
                           content_type="application/json")
    assert response.status_code == 413


def test_report_accepts_the_same_opaque_capability(app_bot, store):
    client, bot, _ = app_bot()
    _, body = token_body(store, report={"pains": {"injury": {"after": 2}}, "minutes": 31})
    assert post(client, body).status_code == 201
    assert len(bot.inbound) == 1
    assert "Injury : 2/10 après" in bot.inbound[0][1]


def test_revoked_capability_cannot_submit_a_report(app_bot, store):
    client, bot, _ = app_bot()
    token, body = token_body(store)
    assert store.revoke_strength_link("4242", token)
    response = post(client, body)
    assert response.status_code == 410
    assert bot.inbound == []


def test_report_refuses_fields_outside_the_capability_contract(app_bot, store):
    client, bot, _ = app_bot()
    _, body = token_body(store)
    body["payload"] = "ancien-format"
    assert post(client, body).status_code == 400
    assert bot.inbound == []


def test_report_refuses_a_missing_or_unknown_capability(app_bot):
    client, bot, _ = app_bot()
    report = {"performed_on": clock.today().isoformat()}
    assert post(client, {"report": report}).status_code == 400
    assert post(client, {"token": strength.new_token(), "report": report}).status_code == 403
    assert bot.inbound == []


def test_report_refuses_invalid_json(app_bot):
    client, bot, _ = app_bot()
    assert post(client, "pas du json").status_code == 400
    assert bot.inbound == []


def test_report_body_is_bounded_before_json_decoding(app_bot):
    client, bot, _ = app_bot()
    response = client.post("/strength-report", data=b"x" * (32 * 1024 + 1),
                           content_type="application/json")
    assert response.status_code == 413
    assert bot.inbound == []


def test_a_session_can_only_be_reported_once(app_bot, store):
    """Rejouer le même lien coûte une réponse, jamais un second tour agent."""
    client, bot, _ = app_bot()
    _, body = token_body(store, report={"pains": {"injury": {"after": 1}}})
    assert post(client, body).status_code == 201
    assert post(client, body).status_code == 200
    assert len(bot.inbound) == 1


def test_daily_cap_stops_a_flood(app_bot, store):
    client, bot, _ = app_bot(strength_reports_per_day=2)
    for index in range(5):
        _, body = token_body(store, session=dict(SESSION, sid=f"s{index}"))
        response = post(client, body)
    assert len(bot.inbound) == 2
    assert response.status_code == 429


def test_capabilities_need_no_shared_link_secret(app_bot, store):
    client, bot, _ = app_bot()
    _, body = token_body(store)
    assert post(client, body).status_code == 201
    assert len(bot.inbound) == 1


def test_report_route_is_absent_without_an_owner(app_bot, store):
    client, bot, _ = app_bot(allowed_psid="")
    _, body = token_body(store)
    assert post(client, body).status_code == 404
    assert bot.inbound == []


def test_the_report_lands_in_the_typed_log_too(app_bot, store):
    """La route transmet une structure relationnelle au repository PostgreSQL."""
    client, bot, _ = app_bot()
    _, body = token_body(store, report={"pains": {"injury": {"after": 1}}, "exercises": [
        {"i": 0, "score": 6, "reps": [14, 12, 11, 10]}]})
    post(client, body)

    payload = next(iter(bot.operational_store.sessions.values()))
    exercise = payload["exercises"][0]
    assert exercise["exercise_slug"] == "calf-raise-bent"
    assert exercise["difficulty"] == 6
    assert [(row["side"], row["set_index"], row["repetitions"]) for row in exercise["sets"]] == [
        ("right", 1, 14), ("left", 1, 12), ("right", 2, 11), ("left", 2, 10)]


def test_zero_difficulty_is_a_measurement_not_an_absence(app_bot, store):
    client, bot, _ = app_bot()
    _, body = token_body(store, report={"exercises": [{"i": 0, "score": 0}]})
    post(client, body)
    payload = next(iter(bot.operational_store.sessions.values()))
    assert payload["exercises"][0]["difficulty"] == 0


def test_a_replayed_report_does_not_double_the_log(app_bot, store):
    client, bot, _ = app_bot()
    _, body = token_body(store, report={"exercises": [{"i": 0, "reps": [14, 12]}]})
    post(client, body)
    post(client, body)
    assert len(bot.operational_store.sessions) == 1


def test_an_unwritable_log_is_retryable_and_not_claimed(app_bot, store, monkeypatch):
    client, bot, _ = app_bot()
    monkeypatch.setattr(bot.operational_store, "accept_strength_report",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("base indisponible")))
    _, body = token_body(store, report={"pains": {"injury": {"after": 2}}})
    assert post(client, body).status_code == 503
    assert bot.inbound == []


def test_invalid_report_is_rejected_instead_of_silently_clamped(app_bot, store):
    client, bot, _ = app_bot()
    _, body = token_body(store, report={"pains": {"injury": {"after": 99}}})
    response = post(client, body)
    assert response.status_code == 400
    assert response.get_json()["status"] == "invalid"
    assert bot.inbound == []
