"""The HTTP facade: Messenger and Strava webhooks, the session page, healthcheck.

Structural decision: immediate HTTP 200, async processing. Claude may take
minutes; Meta replays any webhook left without a fast response.

La page humaine est une coquille sans donnée personnelle. Une capacité dans le
fragment résout une séance immuable (`/s`) ; le secret n'atteint donc ni les logs
ni le Referer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from scripts.model import strength_log
from scripts.storage.database import expected_schema_revision
from scripts.storage.repository import OperationalStore

from . import clock, page, strength
from .attachments import encode_payload
from .bot import CoachBot
from .config import Config
from .notebook import NotebookService
from .notebook_web import CSP as NOTEBOOK_CSP
from .notebook_web import register as register_notebook

# /s serves the notebook's own document since the guided session became one of its
# screens (2026-10-09): same bytes, same policy. Nothing loads from anywhere else;
# `connect-src 'self'` is what lets it resolve the link and post its report back.
PAGE_CSP = NOTEBOOK_CSP
STRENGTH_REPORT_MAX_BYTES = 32 * 1024
STRENGTH_SESSION_MAX_BYTES = 1024
# Plafond par défaut de tout corps de requête. Sans lui, Waitress accepte 1 Gio et
# `get_data()` le charge en mémoire avant la moindre vérification de signature.
# Les routes plus étroites (renfo, carnet) abaissent encore ce plafond.
REQUEST_MAX_BYTES = 1024 * 1024
# Un challenge de handshake est un nombre (Meta) ou une chaîne aléatoire (Strava).
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{1,128}")

log = logging.getLogger("redline")


def _token_matches(expected: str, given: str | None) -> bool:
    """Comparaison en temps constant ; un secret absent n'ouvre jamais rien."""
    return bool(expected) and hmac.compare_digest(expected.encode(), (given or "").encode())


def _handshake_challenge(expected_token: str) -> str | None:
    """Le challenge `hub.*` à renvoyer, ou None si le handshake est refusé."""
    challenge = request.args.get("hub.challenge", "")
    if (request.args.get("hub.mode") == "subscribe"
            and _token_matches(expected_token, request.args.get("hub.verify_token"))
            and _CHALLENGE.fullmatch(challenge)):
        return challenge
    return None


def _signature_valid(secret: str, raw_body: bytes, header: str) -> bool:
    """Verify the X-Hub-Signature-256 signature stamped by Meta.

    Missing configuration is never an authentication mode.
    """
    if not secret:
        return False
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


def _iter_events(payload: dict):
    """(psid, mid, kind, text, reply_to_mid) for each usable event.

    ``reply_to_mid`` est la relation créée par « Répondre » dans Messenger, et non
    une heuristique tirée du texte. Elle est vide pour les événements qui n'en ont pas.
    ``kind`` appartient à {"text", "sticker", "attachment", "postback"}.

    Stickers are distinguished from real attachments because a human does not reply
    "I can't read your attachment" to a thumbs-up. A postback carries in its payload
    the sentence to inject as if Sam had typed it.
    """
    if payload.get("object") != "page":
        return
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            psid = str(event.get("sender", {}).get("id", ""))
            if not psid:
                continue
            postback = event.get("postback") or {}
            if postback.get("payload"):
                mid = postback.get("mid") or f"pb:{event.get('timestamp', '')}"
                yield psid, mid, "postback", postback["payload"], ""
                continue
            message = event.get("message") or {}
            if message.get("is_echo"):
                continue
            mid = message.get("mid")
            reply_to = message.get("reply_to") or {}
            reply_to_mid = str(reply_to.get("mid") or "")
            quick_reply = message.get("quick_reply") or {}
            if quick_reply.get("payload"):
                yield psid, mid, "postback", quick_reply["payload"], reply_to_mid
                continue
            attachments = message.get("attachments") or []
            text = message.get("text", "").strip()
            if any((att.get("payload") or {}).get("sticker_id") for att in attachments):
                yield psid, mid, "sticker", "", reply_to_mid
            elif attachments:
                yield psid, mid, "attachment", encode_payload(attachments, text), reply_to_mid
            elif text:
                yield psid, mid, "text", text, reply_to_mid


def _exercise_cues(vault: Path) -> dict:
    """{slug: [consignes]} pour la page, lu une fois au démarrage.

    Jamais fatal : une note d'exercice cassée doit faire échouer `vault-check.py`
    et le rendu de la carte, pas le démarrage du service. Sans consignes le bouton
    « Le geste » n'apparaît simplement pas, et la séance se déroule comme avant.
    """
    try:
        from scripts.model.programme import load
        notes = load(vault).exercises
    except Exception as exc:
        log.warning("Consignes d'exécution non chargées (%s) : %s", type(exc).__name__, exc)
        return {}
    return {slug: note["how"] for slug, note in notes.items() if note.get("how")}


def _strength_payload(session: dict, report: dict, rows: list[dict], *,
                      psid: str, message: str) -> dict:
    performed_on = date.fromisoformat(report["performed_on"])
    exercises = []
    for row in rows:
        sets = []
        for column, side in (("valeurs", "both"), ("droite", "right"), ("gauche", "left")):
            for index, repetitions in enumerate(strength_log.series(row.get(column)), 1):
                sets.append({"side": side, "set_index": index, "repetitions": repetitions})
        source_index = next(index for index, exercise in enumerate(session["exercises"])
                            if exercise.get("slug") == row["slug"])
        item = report["items"][source_index]
        slots = (session["exercises"][source_index].get("dose") or {}).get("slots") or []
        if slots:
            sets = [{"side": {"d": "right", "g": "left"}.get(slot.get("s"), "both"),
                     "set_index": slot["n"], "repetitions": value}
                    for slot, value in zip(slots, item["slot_values"], strict=False)
                    if value is not None]
        exercises.append({
            "exercise_slug": row["slug"],
            "difficulty": int(row["score"]) if row.get("score") is not None else None,
            "skipped": row.get("passe") == strength_log.YES,
            "sets": sets,
            "status": row.get("status"),
            "completed_slots": row.get("completed_slots"),
            "note": row.get("note", ""),
        })
    return {
        "session_key": str(session.get("sid") or "")[:64],
        "performed_on": performed_on,
        "workout": str(session.get("session") or "")[:20] or None,
        "shin_before": report["shin_before"],
        "shin_after": report["shin"],
        "duration_minutes": report["minutes"],
        "note": report["note"],
        "prescription": session,
        "exercises": exercises,
        "message_id": f"strength:{session['sid']}",
        "psid": psid,
        "message": message,
    }


def _accept_strength_report(payload: dict, operational_store: OperationalStore,
                            max_per_day: int) -> str | None:
    """Atomically claim and persist a signed report; ``None`` means retryable failure."""
    try:
        accepted = operational_store.accept_strength_report(
            payload, max_per_day)
    except Exception as exc:
        log.error("Journal renfo non écrit (%s) : %s", type(exc).__name__, exc)
        return None
    if accepted not in {"accepted", "duplicate", "quota"}:
        log.error("Journal renfo : statut repository inconnu %r", accepted)
        return None
    return accepted


def create_app(config: Config, bot: CoachBot, state: Any,
               operational_store: OperationalStore | None = None,
               notebook: NotebookService | None = None,
               nudge: Callable[[], None] | None = None) -> Flask:
    """`nudge` réveille le planificateur proactif sans attendre son prochain tick."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = REQUEST_MAX_BYTES
    vault = Path(config.vault_path)
    cues = _exercise_cues(vault)

    def wake() -> None:
        if nudge is not None:
            nudge()

    def hasten_debrief(activity_id: int, why: str) -> None:
        """Sam a fini avec sa sortie : son débrief part sans attendre le délai."""
        if state.proactive.hasten_job(f"strava:{activity_id}", time.time()):
            log.info("Strava activity %s: debrief brought forward (%s)", activity_id, why)
            wake()
    if notebook is not None:
        # Un ressenti qui franchit un plafond ne reste pas dans le carnet : le coach
        # le reçoit comme un message, exactement comme un compte rendu de renfo.
        register_notebook(app, notebook, config.allowed_psid, alert=lambda text, key: (
            bot.submit_event(config.allowed_psid, "postback", text, message_id=key)),
            place=config.instance.place, cues=cues,
            settled=lambda activity_id: hasten_debrief(activity_id, "ressentis du carnet"))
    try:
        schema_expected = expected_schema_revision(
            Path(__file__).resolve().parents[2] / "alembic.ini")
    except Exception as exc:
        log.error("Tête Alembic illisible au démarrage : %s", exc)
        schema_expected = "unknown"

    @app.get("/webhook")
    def verify():
        challenge = _handshake_challenge(config.verify_token)
        if challenge is None:
            return "forbidden", 403
        # Du texte, jamais du HTML : le challenge vient de la requête, et cette origine
        # porte aussi le cookie du carnet.
        response = app.response_class(challenge, mimetype="text/plain")
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/webhook")
    def receive():
        if not (config.app_secret and config.allowed_psid):
            return "webhook disabled", 503
        raw = request.get_data()
        if not _signature_valid(config.app_secret, raw,
                                request.headers.get("X-Hub-Signature-256", "")):
            return "bad signature", 403
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return "ok", 200
        if not isinstance(payload, dict):
            return "ok", 200

        for psid, mid, kind, text, reply_to_mid in _iter_events(payload):
            if psid != config.allowed_psid:
                continue  # lock: the coach only talks to Sam
            if not mid or not state.claim_inbound(
                    mid, psid, kind, text, reply_to_message_id=reply_to_mid):
                continue
            state.mark_inbound(psid)  # open/refresh the Messenger 24 h window
            bot.submit_event(psid, kind, text, message_id=mid)
        return "ok", 200

    # Strava ne signe pas ses événements et `owner_id` est public (l'URL du profil).
    # L'authentification est donc le secret porté par l'URL de callback, connue de
    # Strava seul ; une URL fausse répond 404 comme une route qui n'existe pas.
    def strava_path_ok(secret: str) -> bool:
        return bool(config.strava_owner_id) and _token_matches(
            config.strava_webhook_secret, secret)

    @app.get("/strava-webhook/<secret>")
    def strava_verify(secret):
        if not (strava_path_ok(secret) and config.strava_verify_token):
            return "not found", 404
        # Strava validates the subscription with the same hub.* scheme as Meta, but
        # expects the challenge returned as JSON.
        challenge = _handshake_challenge(config.strava_verify_token)
        if challenge is None:
            return "forbidden", 403
        return jsonify({"hub.challenge": challenge}), 200

    @app.post("/strava-webhook/<secret>")
    def strava_event(secret):
        if not strava_path_ok(secret):
            return "not found", 404
        try:
            event = json.loads(request.get_data())
        except ValueError:
            return "ok", 200
        if not isinstance(event, dict):
            return "ok", 200
        aspect = event.get("aspect_type")
        if event.get("object_type") != "activity" or aspect not in {"create", "update"}:
            return "ok", 200
        # Le chemin secret authentifie l'émetteur ; ce filtre écarte encore les
        # événements d'un autre athlète qui aurait autorisé l'application.
        owner = str(event.get("owner_id", ""))
        if owner != config.strava_owner_id:
            log.warning("Strava event ignored: unknown owner_id %s", owner or "(absent)")
            return "ok", 200
        activity_id = event.get("object_id")
        if type(activity_id) is not int or activity_id <= 0:
            log.warning("Strava event ignored: invalid object_id %r", activity_id)
            return "ok", 200
        if aspect == "create":
            # Le délai n'est plus qu'un filet : la sortie est capturée tout de suite,
            # et son débrief part dès que Sam montre qu'il a fini (titre renommé,
            # ressentis donnés dans le carnet, message au coach).
            due = time.time() + config.strava_debrief_delay
            state.proactive.record_activity(str(activity_id), due)
            log.info("Strava activity %s received: debrief at the latest in %d s",
                     activity_id, config.strava_debrief_delay)
            wake()
        elif isinstance(event.get("updates"), dict) and "title" in event["updates"]:
            # Titre et description s'enregistrent ensemble dans Strava : un titre
            # renommé dit que Sam a écrit ce qu'il voulait dire de sa sortie. Sans
            # débrief en attente (vieille sortie retouchée), il ne se passe rien.
            hasten_debrief(activity_id, "titre renommé")
        return "ok", 200

    # ---- interactive strength session

    session_html, session_etag = page.build(vault / "scripts" / "cards", cues)

    @app.get(strength.PAGE_PATH)
    def session_page():
        """The page itself: the same bytes for everyone, assembled once."""
        response = app.response_class(session_html, mimetype="text/html")
        response.set_etag(session_etag)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Security-Policy"] = PAGE_CSP
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response.make_conditional(request)

    @app.post("/strength-session")
    def strength_session():
        """Resolve an opaque fragment capability without putting it in access logs."""
        if not (config.coach_base_url and config.allowed_psid):
            return "not found", 404
        request.max_content_length = STRENGTH_SESSION_MAX_BYTES
        try:
            body = json.loads(request.get_data())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "bad request", 400
        if not isinstance(body, dict) or set(body) != {"token"}:
            return "bad request", 400
        resolved = state.resolve_strength_link(
            config.allowed_psid, str(body.get("token") or ""))
        status = resolved.get("status")
        if status in {"expired", "revoked"}:
            response = jsonify({"status": "expired"})
            response.status_code = 410
        elif status != "active":
            response = jsonify({"status": "forbidden"})
            response.status_code = 403
        else:
            previous = None
            if operational_store is not None:
                previous = operational_store.previous_strength_prescription(resolved["session"], clock.today())
            response = jsonify({"status": "ok", "session": resolved["session"],
                                "previous_session": previous})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/strength-report")
    def strength_report():
        """The end of the session, posted back by the page.

        Everything is refused by default: no PSID, no capability, no session -- no
        injection. What survives all that is parsed once into bounded
        fields, then feeds both typed storage and the athlete's durable queue,
        exactly like a persistent-menu postback.
        """
        if not (config.allowed_psid and config.coach_base_url):
            return "not found", 404
        # Limite locale à cette route : un webhook Meta légitime peut être plus
        # gros et ne doit pas hériter du plafond minuscule d'un formulaire renfo.
        request.max_content_length = STRENGTH_REPORT_MAX_BYTES
        try:
            body = json.loads(request.get_data())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "bad request", 400
        if not isinstance(body, dict):
            return "bad request", 400

        if set(body) != {"token", "report"}:
            return "bad request", 400
        token = str(body.get("token") or "")
        resolved = state.resolve_strength_link(config.allowed_psid, token)
        if resolved.get("status") in {"expired", "revoked"}:
            return jsonify({"status": "expired"}), 410
        if resolved.get("status") != "active":
            log.warning("Strength report refused: unknown capability")
            return "forbidden", 403
        session = resolved["session"]
        try:
            strength.validate_session(session)
        except strength.ExpiredLink as exc:
            log.info("Strength report refused: %s", exc)
            return jsonify({"status": "expired"}), 410
        except ValueError as exc:
            log.warning("Strength report refused: %s", exc)
            return "bad request", 400

        report = body.get("report")
        try:
            clean = strength.parse_report(report, session["exercises"])
            if not clean["performed_on"]:
                raise strength.ReportError("performed_on absent")
            performed_on = date.fromisoformat(clean["performed_on"])
            today = clock.today()
            if not today - timedelta(days=7) <= performed_on <= today:
                raise strength.ReportError("performed_on hors de la fenêtre autorisée")
        except strength.ReportError as exc:
            log.warning("Strength report refused: %s", exc)
            return jsonify({"status": "invalid", "error": str(exc)}), 400
        if operational_store is None:
            return "temporary failure", 503
        rows = strength.log_entries(session, clean, performed_on.isoformat())
        message = strength.format_message(session, clean)
        payload = _strength_payload(
            session, clean, rows, psid=config.allowed_psid, message=message)
        status = _accept_strength_report(
            payload, operational_store=operational_store,
            max_per_day=config.strength_reports_per_day)
        if status is None:
            return "temporary failure", 503
        sid = session["sid"]
        if status == "duplicate":
            log.info("Strength report replay ignored: %s", sid)
            return jsonify({"status": "duplicate"}), 200
        if status == "quota":
            log.warning("Strength report daily quota reached: %s", sid)
            return jsonify({"status": "quota"}), 429

        bot.submit_event(config.allowed_psid, "postback", message,
                         message_id=f"strength:{sid}")
        log.info("Strength report accepted: %s", sid)
        return jsonify({"status": "accepted"}), 201

    # Read ONCE, at boot. `commit` is the checkout the process started on, which the
    # coach's own vault commits move afterwards; `runtime_tree` fingerprints the CODE
    # (server/, scripts/, migrations/), which only a deployment changes. The release
    # check compares the latter.
    booted_commit = bot.repo.head()[:12]
    runtime_tree = bot.repo.runtime_tree()
    booted_at = time.time()

    @app.get("/health")
    def health():
        """Liveness: the HTTP process answers and identifies its deployed code."""
        return jsonify({
            "status": "ok",
            "commit": booted_commit or "unknown",
            "runtime_tree": runtime_tree or "unknown",
            "uptime_s": int(time.time() - booted_at),
        }), 200

    @app.get("/ready")
    def ready():
        """Readiness: dependencies required to accept a new message are usable."""
        schema_current = operational_store.schema_revision() if operational_store else None
        schema_ok = bool(schema_current and schema_current == schema_expected)
        storage_ok = bool(operational_store and operational_store.ready(schema_expected))
        worker_ok = bot.is_running()
        status = 200 if storage_ok and schema_ok and worker_ok else 503
        return jsonify({
            "status": "ready" if status == 200 else "unavailable",
            "storage": storage_ok,
            "schema": schema_ok,
            "revision": schema_current,
            "expected_revision": schema_expected,
            "worker": worker_ok,
        }), status

    return app
