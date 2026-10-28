"""Routes privées du carnet ; seules la coquille et les ressources sont publiques."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from redline.cards import render
from redline.coach.fr import session_labels
from redline.storage.database import StorageError
from redline.storage.notebook import DEVICE_SECONDS

from . import clock, page
from . import notebook_weather as weather
from .notebook import NotebookService
from .notebook_board import AnswerError

WEEK = re.compile(r"20\d{2}-W(?:0[1-9]|[1-4]\d|5[0-3])")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ZONE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
ASK = re.compile(r"(?:cell:[0-9a-f-]{36}:(?:pain:[a-z0-9][a-z0-9-]{0,39}|rpe|enjoyment)"
                 r"|wake-cell:[0-9a-f-]{36}:[a-z0-9][a-z0-9-]{0,39}"
                 r"|reserve:[0-9a-f-]{36}"
                 r"|wake:20\d{2}-\d{2}-\d{2}:[a-z0-9][a-z0-9-]{0,39}"
                 r"|daily:20\d{2}-\d{2}-\d{2}:(?:sleep|life_load)|hr:20\d{2}-\d{2}-\d{2})")

COOKIE = "__Host-redline-notebook"
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "font-src data:; img-src 'self' data:; connect-src 'self'; manifest-src 'self'; "
       "worker-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


PLACE = re.compile(r"[\w' -]{1,60}")
DAY = re.compile(r"20\d{2}-\d{2}-\d{2}")
CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def register(app: Flask, notebook: NotebookService, psid: str,
             alert: Callable[[str, str], None] | None = None, place: str = "Paris",
             cues: dict | None = None,
             settled: Callable[[int], None] | None = None) -> None:
    """`alert(texte, clé)` réveille le coach quand une réponse franchit un plafond ;
    `cues` porte les consignes d'exécution des exercices, pour la séance guidée ;
    `settled(activité)` avance le débrief d'une sortie dont les ressentis sont complets."""
    document = page.notebook_document(render.CARDS_DIR, cues,
                                      session_labels.for_page()).encode("utf-8")
    # La coquille ne porte aucune donnée : elle se revalide (304) et voyage compressée.
    # Mesure du 2026-10-08 : 162 Ko re-téléchargés à chaque ouverture, en `no-store`.
    document_etag = hashlib.sha256(document).hexdigest()[:16]
    document_gzip = gzip.compress(document, mtime=0)
    origin = urlsplit(notebook.base_url)
    expected_origin = f"{origin.scheme}://{origin.netloc}"

    @app.after_request
    def notebook_headers(response):
        if request.path.startswith("/carnet"):
            response.headers.update({"Cache-Control": "no-cache" if request.path == "/carnet"
                                     else response.headers.get("Cache-Control", "no-store")
                                     if request.path.startswith("/carnet-launch/") else "no-store",
                                     "Referrer-Policy": "no-referrer",
                                     "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": CSP})
        return response

    @app.before_request
    def notebook_access():
        if not request.path.startswith("/carnet-api/"):
            return None
        request.max_content_length = 2048
        if request.method == "POST":
            if not request.is_json or request.headers.get("Origin", expected_origin) != expected_origin:
                return jsonify(error="Accès refusé."), 403
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="Accès refusé."), 403
        if request.path == "/carnet-api/connect":
            return None
        try:
            authenticated = notebook.store.authenticate(psid, request.cookies.get(COOKIE, ""))
        except (StorageError, SQLAlchemyError):
            return jsonify(error="Carnet temporairement indisponible. Réessaie dans un instant."), 503
        if not authenticated:
            return jsonify(error="Ouvre un nouveau lien avec /carnet dans Messenger."), 401
        return None

    @app.errorhandler(StorageError)
    @app.errorhandler(SQLAlchemyError)
    def notebook_database_error(error):
        app.logger.error("Stockage indisponible : %s", type(error).__name__)
        return jsonify(error="Stockage temporairement indisponible."), 503

    @app.get("/carnet")
    def notebook_page():
        compressed = "gzip" in request.headers.get("Accept-Encoding", "")
        response = app.response_class(document_gzip if compressed else document,
                                      mimetype="text/html")
        if compressed:
            response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"
        response.set_etag(document_etag + ("-gz" if compressed else ""))
        return response.make_conditional(request)

    @app.get("/carnet-manifest.json")
    def notebook_manifest():
        return jsonify(name="Carnet Redline", short_name="Redline", lang="fr",
                       start_url="/carnet", scope="/", id="/carnet", display="standalone",
                       background_color="#f7f5f2", theme_color="#f7f5f2",
                       icons=[{"src": "/carnet-install-icon.png", "sizes": "512x512", "type": "image/png"}])

    # La coquille gardée sur l'appareil : le service worker ne garde que `/carnet`, qui
    # ne porte aucune donnée ; il ne touche jamais aux routes /carnet-api.
    @app.get("/carnet-sw.js")
    def notebook_worker():
        return app.response_class((page.PAGE_DIR / "carnet" / "sw.js").read_bytes(),
                                  mimetype="text/javascript")

    @app.get("/carnet-launch/<name>.png")
    def notebook_launch_image(name):
        image = page.launch_image(name)
        if image is None:
            return "not found", 404
        response = app.response_class(image, mimetype="image/png")
        response.headers["Cache-Control"] = "public, max-age=604800"
        return response

    @app.get("/carnet-install-icon.png")
    def notebook_install_icon():
        return app.response_class((page.PAGE_DIR / "notebook-icon.png").read_bytes(), mimetype="image/png")

    @app.get("/carnet-icon.png")
    def notebook_icon():
        return app.response_class((page.PAGE_DIR / "icon.png").read_bytes(), mimetype="image/png")

    @app.post("/carnet-api/connect")
    def notebook_connect():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"token"} or not isinstance(body["token"], str):
            return jsonify(error="Lien invalide."), 400
        # Une ancienne notification reste ouvrable sur un appareil déjà connecté.
        if notebook.store.authenticate(psid, request.cookies.get(COOKIE, "")):
            return jsonify(status="ok")
        device = notebook.store.exchange(psid, body["token"])
        if not device:
            return jsonify(error="Ce lien a expiré ou a déjà servi. Tape /carnet dans Messenger."), 401
        response = jsonify(status="ok")
        response.set_cookie(COOKIE, device, secure=True, httponly=True, samesite="Strict",
                            max_age=DEVICE_SECONDS, path="/")
        return response

    @app.post("/carnet-api/logout")
    def notebook_logout():
        notebook.store.revoke(psid, request.cookies.get(COOKIE, ""))
        response = jsonify(status="ok")
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
        return response

    @app.get("/carnet-api/entries")
    def notebook_entries():
        try:
            offset = int(request.args.get("offset", "0"))
            if not 0 <= offset <= 100000:
                raise ValueError
        except ValueError:
            return jsonify(error="Page invalide."), 400
        rows = notebook.store.list_entries(psid, offset=offset, limit=41)
        return jsonify(entries=rows[:40], next_offset=offset + 40 if len(rows) > 40 else None,
                       latest=[item for kind in ("weekly", "monthly")
                               for item in notebook.store.list_entries(psid, kind=kind, limit=1)])

    @app.get("/carnet-api/current")
    def notebook_current():
        return jsonify(notebook.current())

    @app.get("/carnet-api/today")
    def notebook_today():
        return jsonify(notebook.today())

    @app.get("/carnet-api/week/<label>")
    def notebook_week(label):
        if not WEEK.fullmatch(label):
            return jsonify(error="Semaine invalide."), 400
        return jsonify(notebook.week(psid, label))

    @app.get("/carnet-api/weeks")
    def notebook_weeks():
        return jsonify(weeks=notebook.weeks(psid))

    @app.get("/carnet-api/injury")
    def notebook_injury():
        zone = request.args.get("zone") or None
        if zone is not None and not ZONE.fullmatch(zone):
            return jsonify(error="Zone invalide."), 400
        view = notebook.injury(zone)
        if view is None:
            return jsonify(error="Aucune zone surveillée."), 404
        return jsonify(view)

    @app.get("/carnet-api/session/<observation_id>")
    def notebook_session(observation_id):
        if not UUID.fullmatch(observation_id):
            return jsonify(error="Séance invalide."), 400
        view = notebook.session(observation_id)
        if view is None:
            return jsonify(error="Séance introuvable."), 404
        return jsonify(view)

    @app.get("/carnet-api/race")
    def notebook_race():
        return jsonify(notebook.race(psid))

    @app.get("/carnet-api/weather")
    def notebook_weather():
        day, start = request.args.get("date", ""), request.args.get("time") or None
        where = (request.args.get("place") or "").strip() or place
        if not DAY.fullmatch(day) or (start and not CLOCK.fullmatch(start)) \
                or not PLACE.fullmatch(where):
            return jsonify(error="Demande invalide."), 400
        try:
            when = dt.date.fromisoformat(day)
        except ValueError:
            return jsonify(error="Date invalide."), 400
        return jsonify(weather.forecast(where, when, start, clock.now()) or {})

    @app.post("/carnet-api/answer")
    def notebook_answer():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"ask", "value"}
                or not isinstance(body["ask"], str) or not ASK.fullmatch(body["ask"])):
            return jsonify(error="Réponse invalide."), 400
        try:
            result = notebook.board().answer(body["ask"], body["value"])
        except AnswerError as error:
            return jsonify(error=str(error)), 409
        if result["alert"] and alert is not None:
            try:
                alert(result["alert"], "carnet:" + body["ask"])
            except Exception:
                app.logger.exception("Alerte du carnet non transmise au coach")
        if result.get("activity") and settled is not None:
            try:
                settled(int(result["activity"]))
            except Exception:
                app.logger.exception("Débrief de la sortie non avancé")
        return jsonify(status="ok", alert=bool(result["alert"]))

    @app.post("/carnet-api/launch")
    def notebook_launch():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"signature"} or not isinstance(body["signature"], str):
            return jsonify(error="Demande invalide."), 400
        url = notebook.launch(psid, body["signature"])
        if not url:
            return jsonify(error="La séance a changé ou n’est plus disponible. Actualise le carnet."), 409
        return jsonify(url=url)

    @app.get("/carnet-api/entries/<entry_id>")
    def notebook_entry(entry_id):
        entry = notebook.store.get(psid, entry_id)
        if entry is None:
            return jsonify(error="Carte introuvable."), 404
        entry["related"] = (notebook.store.list_entries(psid, kind="weekly", month=entry["period"], limit=100)
                            if entry["kind"] == "monthly" and entry["period"] else [])
        entry["report"] = None
        snapshot = entry.get("snapshot")
        if snapshot and snapshot.get("sid"):
            entry["report"] = notebook.store.strength_report(snapshot["sid"])
        return jsonify(entry)

    @app.get("/carnet-api/entries/<entry_id>/image")
    def notebook_image(entry_id):
        entry = notebook.store.get(psid, entry_id, image=True)
        if entry is None or entry["image"] is None:
            return "not found", 404
        response = app.response_class(bytes(entry["image"]), mimetype=entry["image_type"])
        response.headers["Content-Disposition"] = f'inline; filename="redline-{entry_id}.png"'
        return response
