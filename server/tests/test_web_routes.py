"""Les routes HTTP elles-mêmes — c'est-à-dire la moitié du service exposée à Internet.

`test_web.py` couvrait les briques (`_iter_events`, `_signature_valid`)
puis sautait directement à `/strength-report`. Entre les deux, aucune garde d'accès
n'était testée AU NIVEAU DE LA ROUTE : ni le handshake Meta, ni le 403 sur signature
invalide, ni le verrou `MESSENGER_ALLOWED_PSID`, ni le filtre `STRAVA_OWNER_ID` dont
`.env.example` dit noir sur blanc « Strava ne signe pas ses events : sans ce filtre,
n'importe quel POST déclencherait un débrief fantôme ».

Une brique correcte qu'une route oublie d'appeler est une brique inutile, et c'est
précisément ce qu'un test unitaire de brique ne peut pas voir (audit 2026-07-29).
"""

import hashlib
import hmac
import json
import time

import pytest
from conftest import RecordingBot

from redline.web import create_app

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
    # Le challenge vient de la requête : rendu en HTML, il devenait un XSS réfléchi
    # sur l'origine du carnet (audit 2026-10-08).
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
    """Rendre le challenge à qui ne connaît pas le jeton laisserait n'importe qui
    abonner son propre webhook à la page ; un challenge qui n'est pas un simple
    identifiant n'a rien à faire dans une réponse."""
    api, _, _ = client(verify_token="jeton-attendu")
    assert api.get(f"/webhook?{query}").status_code == 403


def test_an_empty_verify_token_opens_nothing(client):
    api, _, _ = client(verify_token="")
    assert api.get("/webhook?hub.mode=subscribe&hub.verify_token="
                   "&hub.challenge=1234").status_code == 403


def test_an_oversized_body_is_refused_before_any_read(client):
    """Waitress accepte 1 Gio par défaut et `get_data()` le chargeait en mémoire avant
    même de vérifier la signature."""
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
    """403 AVANT de toucher au bot : le corps n'est même pas parsé."""
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    response = api.post("/webhook", json=message())
    assert response.status_code == 403
    assert bot.inbound == []


def test_a_forged_signature_is_refused(client):
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    assert post_signed(api, message(), secret="pas-le-bon").status_code == 403
    assert bot.inbound == []


def test_the_psid_lock_drops_a_stranger(client):
    """LE verrou : le coach ne parle qu'à Sam. Un inconnu qui écrit à la page
    déclencherait sinon un run Claude sur son vault à lui."""
    api, bot, _ = client(app_secret=SECRET, allowed_psid="4242")
    assert post_signed(api, message(psid="9999")).status_code == 200
    assert bot.inbound == []


def test_without_a_configured_psid_no_message_passes(client):
    api, bot, _ = client(app_secret=SECRET, allowed_psid="")
    assert post_signed(api, message(psid="9999")).status_code == 503
    assert bot.inbound == []


def test_a_replayed_message_id_runs_claude_once(client):
    """Meta livre « au moins une fois » : sans la déduplication au niveau de la
    route, une relivraison est un deuxième run Claude sur le même texto."""
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
    """200 sur du JSON illisible : un 500 fait retenter Meta en boucle."""
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
    """`mark_inbound` est ce qui autorise le coach à répondre : sans lui, la fenêtre
    Messenger se referme et les proactifs sont refusés."""
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
    """Strava attend le challenge en JSON, pas en texte brut comme Meta."""
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
    """LE contrôle d'accès de cette route : Strava ne signe pas ses events et
    `owner_id` se lit dans l'URL publique du profil. Sans le secret, n'importe quel
    POST planifiait un débrief et un appel Strava (audit 2026-10-08)."""
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
    """Le 200 est volontaire — on ne renseigne pas l'émetteur."""
    api, _, _ = strava()
    assert api.post(STRAVA_URL, json=_activity(owner=owner)).status_code == 200
    assert store.proactive.jobs(active_only=True) == []


def test_a_non_creation_event_schedules_nothing(strava, store):
    """Une mise à jour de titre ne se débriefe pas."""
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
    """Titre et description s'enregistrent ensemble : renommer, c'est avoir fini."""
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
    """Ce que healthchecks.io et Caddy interrogent."""
    api, _, _ = client()
    assert api.get("/health").status_code == 200


def test_health_reports_the_running_commit(client):
    """Le trou qui a coûté 7 jours de prompts périmés : rien ne disait sur quel
    code le service tournait. `/health` doit le dire, sinon le redémarrage
    automatique est une automatisation qu'on ne peut pas vérifier."""
    api, _, _ = client()
    body = api.get("/health").get_json()
    assert body["status"] == "ok"
    assert body["commit"] == "0123456789ab"     # tronqué à 12, comme `git log --oneline`
    assert body["runtime_tree"] == "0123456789ab"  # l'empreinte que compare la release
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
