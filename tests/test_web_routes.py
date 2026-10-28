"""The HTTP routes themselves: the half of the service exposed to the Internet.

`test_web.py` covers the building blocks (`_iter_events`, `_signature_valid`) and then jumps
straight to `/strength-report`. In between, no access guard would be tested AT ROUTE LEVEL:
neither the Meta handshake, nor the 403 on an invalid signature, nor the
`MESSENGER_ALLOWED_PSID` lock, nor the `STRAVA_OWNER_ID` filter, without which any POST would
trigger a phantom debrief (Strava does not sign its events).

A correct building block that a route forgets to call is a useless block, and that is exactly
what a unit test of the block cannot see.
"""

import hashlib
import hmac
import json
import time

import pytest
from conftest import RecordingBot

from redline.service.web import create_app

SECRET = "app-secret"


@pytest.fixture
def client(make_config, store):
    def factory(**overrides):
        bot = RecordingBot()
        config = make_config(**overrides)
        return create_app(config, bot, store).test_client(), bot, config
    return factory


def post_signed(test_client, payload: dict, secret: str = SECRET, path="/webhook"):
    raw = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return test_client.post(path, data=raw,
                            headers={"X-Hub-Signature-256": f"sha256={signature}"},
                            content_type="application/json")


def message(psid="4242", mid="m1", text="salut coach", reply_to=""):
    body = {"mid": mid, "text": text}
    if reply_to:
        body["reply_to"] = {"mid": reply_to}
    return {"object": "page",
            "entry": [{"messaging": [{"sender": {"id": psid},
                                      "message": body}]}]}


# ------------------------------------------------------- handshake Messenger

def test_verify_returns_the_challenge_on_the_right_token(client):
    api, _, _ = client(verify_token="jeton-attendu")
    response = api.get("/webhook?hub.mode=subscribe&hub.verify_token=jeton-attendu"
                       "&hub.challenge=1234")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "1234"
    # The challenge comes from the request: rendered as HTML it would be a reflected XSS
    # on the notebook origin.
    assert response.mimetype == "text/plain"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("query", [
    "hub.mode=subscribe&hub.verify_token=mauvais&hub.challenge=1234",
    "hub.mode=unsubscribe&hub.verify_token=jeton-attendu&hub.challenge=1234",
    "hub.challenge=1234",
    "hub.mode=subscribe&hub.verify_token=jeton-attendu&hub.challenge=%3Cscript%3E",
    "hub.mode=subscribe&hub.verify_token=jeton-attendu",
])
def test_verify_refuses_anything_else(client, query):
    """Returning the challenge to someone who does not know the token would let anyone
    subscribe their own webhook to the page; a challenge that is not a plain identifier
    has no place in a response."""
    api, _, _ = client(verify_token="jeton-attendu")
    assert api.get(f"/webhook?{query}").status_code == 403


def test_an_empty_verify_token_opens_nothing(client):
    api, _, _ = client(verify_token="")
    assert api.get("/webhook?hub.mode=subscribe&hub.verify_token="
                   "&hub.challenge=1234").status_code == 403


def test_an_oversized_body_is_refused_before_any_read(client):
    """Waitress accepts 1 GiB by default and `get_data()` loaded it into memory before
    even checking the signature."""
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    response = api.post("/webhook", data=b"x" * (1024 * 1024 + 1),
                        content_type="application/json")
    assert response.status_code == 413
    assert bot.inbound == []


# ------------------------------------------------------------- POST /webhook

def test_a_signed_message_reaches_the_bot(client):
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    assert post_signed(api, message()).status_code == 200
    assert bot.inbound == [("4242", "salut coach")]


def test_a_swipe_reply_is_claimed_with_its_target(client, store):
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    store.record_outbound(
        "4242", "Tu sembles vouloir accélérer pour te rassurer.",
        source_key="test:coach-old",
        platform_messages=(("m-coach-old", "Tu sembles vouloir accélérer pour te rassurer."),))

    assert post_signed(api, message(
        mid="m-reply", text="Oui, exactement.", reply_to="m-coach-old")).status_code == 200

    assert bot.inbound == [("4242", "Oui, exactement.")]
    context = store.reply_context(("m-reply",))
    assert "RÉPONSE MESSENGER CIBLÉE" in context
    assert "accélérer pour te rassurer" in context


def test_an_unsigned_message_is_refused_at_the_route(client):
    """403 BEFORE touching the bot: the body is not even parsed."""
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    response = api.post("/webhook", json=message())
    assert response.status_code == 403
    assert bot.inbound == []


def test_a_forged_signature_is_refused(client):
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    assert post_signed(api, message(), secret="pas-le-bon").status_code == 403
    assert bot.inbound == []


def test_the_psid_lock_drops_a_stranger(client):
    """THE lock: the coach only talks to the athlete. A stranger writing to the page would
    otherwise trigger an agent run on their own vault."""
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    assert post_signed(api, message(psid="9999")).status_code == 200
    assert bot.inbound == []


def test_without_a_configured_psid_no_message_passes(client):
    api, bot, _ = client(app_secret=SECRET, allowed_psid="")
    assert post_signed(api, message(psid="9999")).status_code == 503
    assert bot.inbound == []


def test_a_replayed_message_id_runs_claude_once(client):
    """Meta delivers "at least once": without deduplication at route level, a redelivery
    is a second agent run on the same text message."""
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    post_signed(api, message(mid="m1"))
    post_signed(api, message(mid="m1"))
    assert bot.inbound == [("4242", "salut coach")]


def test_a_replayed_message_stays_claimed_after_app_recreation(client, store):
    first, first_bot, config = client(app_secret=SECRET, allowed_psid="4242")
    post_signed(first, message(mid="durable-1"))

    second_bot = RecordingBot()
    second = create_app(config, second_bot, store).test_client()
    post_signed(second, message(mid="durable-1"))

    assert first_bot.inbound == [("4242", "salut coach")]
    assert second_bot.inbound == []


def test_a_malformed_body_is_swallowed_not_crashed(client):
    """200 on unreadable JSON: a 500 makes Meta retry in a loop."""
    api, _, _ = client(app_secret=SECRET)
    raw = b"pas du json"
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert api.post("/webhook", data=raw, headers={
        "X-Hub-Signature-256": f"sha256={signature}"}).status_code == 200


def test_a_valid_non_object_json_body_is_ignored(client):
    api, bot, _ = client(app_secret=SECRET)
    assert post_signed(api, ["not", "an", "event"]).status_code == 200
    assert bot.inbound == []


def test_a_sticker_is_acknowledged_and_a_photo_answered(client):
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    sticker = {"object": "page", "entry": [{"messaging": [
        {"sender": {"id": "4242"},
         "message": {"mid": "s1", "attachments": [
             {"type": "image", "payload": {"sticker_id": 369239263222822}}]}}]}]}
    photo = {"object": "page", "entry": [{"messaging": [
        {"sender": {"id": "4242"},
         "message": {"mid": "p1", "attachments": [{"type": "image"}]}}]}]}
    post_signed(api, sticker)
    post_signed(api, photo)
    assert bot.acknowledged == ["4242"]
    assert len(bot.attachments) == 1
    psid, payload, message_id = bot.attachments[0]
    assert (psid, message_id) == ("4242", "p1")
    assert '"type":"image"' in payload


def test_an_inbound_message_opens_the_24h_window(client, store):
    """`mark_inbound` is what allows the coach to reply: without it, the Messenger window
    closes and proactive messages are refused."""
    api, _, _ = client(app_secret=SECRET, allowed_psid="4242")
    assert store.last_inbound("4242") == 0
    post_signed(api, message())
    assert store.last_inbound("4242") > 0


# -------------------------------------------------------------------- Strava

STRAVA_PATH_SECRET = "s" * 43
STRAVA_URL = f"/strava-webhook/{STRAVA_PATH_SECRET}"


@pytest.fixture
def strava(client):
    def factory(**overrides):
        values = {"strava_owner_id": "777", "strava_webhook_secret": STRAVA_PATH_SECRET,
                  **overrides}
        return client(**values)
    return factory


def test_strava_verify_answers_in_json(strava):
    """Strava expects the challenge as JSON, not as plain text like Meta."""
    api, _, _ = strava(strava_verify_token="jeton-strava")
    response = api.get(f"{STRAVA_URL}?hub.mode=subscribe"
                       "&hub.verify_token=jeton-strava&hub.challenge=abcd")
    assert response.status_code == 200
    assert response.get_json() == {"hub.challenge": "abcd"}


def test_strava_verify_refuses_a_wrong_token(strava):
    api, _, _ = strava(strava_verify_token="jeton-strava")
    assert api.get(f"{STRAVA_URL}?hub.mode=subscribe"
                   "&hub.verify_token=faux&hub.challenge=abcd").status_code == 403


def _activity(owner="777", object_id=90000000007):
    return {"object_type": "activity", "aspect_type": "create",
            "owner_id": owner, "object_id": object_id}


def test_an_activity_from_the_athlete_schedules_a_debrief(strava, store):
    api, _, _ = strava()
    assert api.post(STRAVA_URL, json=_activity()).status_code == 200
    pending = store.proactive.jobs(active_only=True)[0]
    assert pending["detail"] == "90000000007"


@pytest.mark.parametrize("path", [
    "/strava-webhook", "/strava-webhook/faux", f"/strava-webhook/{'t' * 43}"])
def test_strava_events_need_the_secret_path(strava, store, path):
    """THE access control of this route: Strava does not sign its events and `owner_id` can
    be read in the public profile URL. Without the secret, any POST would schedule a debrief
    and a Strava call."""
    api, _, _ = strava(strava_verify_token="jeton-strava")
    assert api.post(path, json=_activity()).status_code == 404
    assert api.get(f"{path}?hub.mode=subscribe&hub.verify_token=jeton-strava"
                   "&hub.challenge=abcd").status_code == 404
    assert store.proactive.jobs(active_only=True) == []


@pytest.mark.parametrize("overrides", [
    {"strava_owner_id": ""}, {"strava_webhook_secret": ""}])
def test_strava_route_is_disabled_without_its_locks(strava, store, overrides):
    api, _, _ = strava(**overrides)
    assert api.post(STRAVA_URL, json=_activity()).status_code == 404
    assert api.post("/strava-webhook/", json=_activity()).status_code == 404
    assert store.proactive.jobs(active_only=True) == []


@pytest.mark.parametrize("owner", ["888", ""])
def test_an_activity_from_anyone_else_is_ignored(strava, store, owner):
    """The 200 is deliberate: the sender is told nothing."""
    api, _, _ = strava()
    assert api.post(STRAVA_URL, json=_activity(owner=owner)).status_code == 200
    assert store.proactive.jobs(active_only=True) == []


def test_a_non_creation_event_schedules_nothing(strava, store):
    """A title update is not debriefed."""
    api, _, _ = strava()
    event = dict(_activity(), aspect_type="update", updates={"title": "Footing"})
    api.post(STRAVA_URL, json=event)
    assert store.proactive.jobs(active_only=True) == []


def _nudged(make_config, store, **overrides):
    nudges = []
    config = make_config(strava_owner_id="777", strava_webhook_secret=STRAVA_PATH_SECRET,
                         **overrides)
    api = create_app(config, RecordingBot(), store,
                     nudge=lambda: nudges.append(1)).test_client()
    return api, nudges


def test_an_arrival_wakes_the_scheduler_to_capture_it_at_once(make_config, store):
    api, nudges = _nudged(make_config, store)
    api.post(STRAVA_URL, json=_activity())
    assert nudges == [1]


def test_a_renamed_activity_brings_its_debrief_forward(make_config, store):
    """Title and description are saved together: renaming means being done."""
    api, nudges = _nudged(make_config, store)
    api.post(STRAVA_URL, json=_activity())
    assert store.proactive.jobs(active_only=True)[0]["due"] > time.time() + 200
    api.post(STRAVA_URL, json=dict(_activity(), aspect_type="update",
                                   updates={"title": "Footing des quais"}))
    assert store.proactive.jobs(active_only=True)[0]["due"] <= time.time()
    assert nudges == [1, 1]


@pytest.mark.parametrize("updates", [{"type": "Walk"}, {"private": "true"}, "title", None])
def test_an_update_without_a_title_leaves_the_debrief_waiting(make_config, store, updates):
    api, nudges = _nudged(make_config, store)
    api.post(STRAVA_URL, json=_activity())
    api.post(STRAVA_URL, json=dict(_activity(), aspect_type="update", updates=updates))
    assert store.proactive.jobs(active_only=True)[0]["due"] > time.time() + 200
    assert nudges == [1]


def test_a_rename_from_anyone_else_changes_nothing(make_config, store):
    api, _ = _nudged(make_config, store)
    api.post(STRAVA_URL, json=_activity())
    api.post(STRAVA_URL, json=dict(_activity(owner="888"), aspect_type="update",
                                   updates={"title": "Volé"}))
    assert store.proactive.jobs(active_only=True)[0]["due"] > time.time() + 200


@pytest.mark.parametrize("object_id", [None, "", "90000000007", True, 0, -1])
def test_an_invalid_activity_id_is_ignored(strava, store, object_id):
    api, _, _ = strava()
    api.post(STRAVA_URL, json=_activity(object_id=object_id))
    assert store.proactive.jobs(active_only=True) == []


def test_health_answers(client):
    """What healthchecks.io and Caddy poll."""
    api, _, _ = client()
    assert api.get("/health").status_code == 200


def test_health_reports_the_running_commit(client):
    """Nothing said which code the service was running. `/health` must say it, otherwise the
    automatic restart is an automation that cannot be verified."""
    api, _, _ = client()
    body = api.get("/health").get_json()
    assert body["status"] == "ok"
    assert body["commit"] == "0123456789ab"     # truncated to 12, like `git log --oneline`
    assert body["runtime_tree"] == "0123456789ab"  # the fingerprint that the release compares
    assert body["uptime_s"] >= 0


def test_ready_refuses_traffic_without_operational_storage(client):
    api, _, _ = client()
    response = api.get("/ready")
    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "unavailable"
    assert body["storage"] is False
    assert body["schema"] is False
    assert body["revision"] is None
    assert body["expected_revision"] != "unknown"
    assert body["worker"] is True


def test_ready_refuses_a_reachable_database_with_an_old_schema(client):
    class OldStore:
        def schema_revision(self):
            return "ancienne"

        def ready(self, expected_revision=None):
            return True

    _, _, config = client()
    response = create_app(
        config, RecordingBot(), object(), OldStore()).test_client().get("/ready")
    assert response.status_code == 503
    assert response.get_json()["schema"] is False
