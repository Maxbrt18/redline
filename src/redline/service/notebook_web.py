"""The notebook API the iOS app calls; every route but the connection needs a device token."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from redline.coach.fr import notebook as notebook_text
from redline.model import formats, iso_week
from redline.model import ledger as ledger_model
from redline.model import notebook_views as views
from redline.model.schema import SchemaError
from redline.storage.database import StorageError

from . import clock, race_plan, reorganize
from . import notebook_weather as weather
from .notebook import NotebookService
from .notebook_board import AnswerError

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ZONE = re.compile(formats.SLUG)
# Every field a `cell:` ask can carry: its list comes from the model, so a metric the
# board asks for is never refused here.
CELL_FIELDS = "|".join(re.escape(field)
                       for field in ledger_model.FEELINGS + ledger_model.FUELLING_METRICS)
ASK = re.compile(rf"(?:cell:[0-9a-f-]{{36}}:(?:pain:{formats.SLUG}|{CELL_FIELDS})"
                 rf"|wake-cell:[0-9a-f-]{{36}}:{formats.SLUG}"
                 rf"|wake:{formats.DATE}:{formats.SLUG}"
                 rf"|daily:{formats.DATE}:(?:sleep|life_load)|hr:{formats.DATE})")

BEARER = "Bearer "
# JSON only: nothing in a response is ever rendered as a document.
CSP = "default-src 'none'; frame-ancestors 'none'"


#: A place name to read the weather of; as long as the instance's own `place` may be.
PLACE = re.compile(r"[\w' -]{1,80}")
DAY = re.compile(formats.DATE)
CLOCK = re.compile(formats.TIME)


def device_token() -> str:
    """The device's token, sent by the app as a bearer token; any other `Authorization`
    header counts as no token at all."""
    header = request.headers.get("Authorization", "")
    return header.removeprefix(BEARER) if header.startswith(BEARER) else ""


def register(app: Flask, notebook: NotebookService, psid: str, *,
             alert: Callable[[str, str], None], place: str,
             settled: Callable[[int], None]) -> None:
    """`alert(text, key)` wakes the coach when an answer crosses a cap;
    `settled(activity)` advances the debrief of a run whose feelings are complete."""
    origin = urlsplit(notebook.base_url)
    expected_origin = f"{origin.scheme}://{origin.netloc}"

    @app.after_request
    def notebook_headers(response):
        if request.path.startswith("/notebook-api/"):
            response.headers.update({"Cache-Control": "no-store",
                                     "Referrer-Policy": "no-referrer",
                                     "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": CSP})
        return response

    @app.before_request
    def notebook_access():
        if not request.path.startswith("/notebook-api/"):
            return None
        request.max_content_length = 2048
        if request.method == "POST":
            if not request.is_json or request.headers.get("Origin", expected_origin) != expected_origin:
                return jsonify(error=notebook_text.ERRORS["forbidden"]), 403
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error=notebook_text.ERRORS["forbidden"]), 403
        if request.path == "/notebook-api/devices":
            return None
        try:
            authenticated = notebook.store.authenticate(psid, device_token())
        except (StorageError, SQLAlchemyError):
            return jsonify(error=notebook_text.ERRORS["unavailable"]), 503
        if not authenticated:
            return jsonify(error=notebook_text.ERRORS["signed_out"]), 401
        return None

    @app.errorhandler(StorageError)
    @app.errorhandler(SQLAlchemyError)
    def notebook_database_error(error):
        app.logger.error("Storage unavailable: %s", type(error).__name__)
        return jsonify(error=notebook_text.ERRORS["storage"]), 503

    @app.post("/notebook-api/devices")
    def notebook_device():
        """The app's connection: the link buys a device token, returned in the body for the
        app's keychain."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"token"} or not isinstance(body["token"], str):
            return jsonify(error=notebook_text.ERRORS["bad_link"]), 400
        device = notebook.store.exchange(psid, body["token"])
        if not device:
            return jsonify(error=notebook_text.ERRORS["link_used"]), 401
        return jsonify(views.DeviceGrant(device=device))

    @app.post("/notebook-api/logout")
    def notebook_logout():
        notebook.store.revoke(psid, device_token())
        return jsonify(views.Done(status="ok"))

    @app.get("/notebook-api/entries")
    def notebook_entries():
        try:
            offset = int(request.args.get("offset", "0"))
            if not 0 <= offset <= 100000:
                raise ValueError
        except ValueError:
            return jsonify(error=notebook_text.ERRORS["bad_page"]), 400
        rows = notebook.store.list_entries(psid, offset=offset, limit=41)
        page: views.EntriesView = {
            "entries": rows[:40], "next_offset": offset + 40 if len(rows) > 40 else None,
            "latest": [item for kind in ("weekly", "monthly")
                       for item in notebook.store.list_entries(psid, kind=kind, limit=1)]}
        return jsonify(page)

    @app.get("/notebook-api/current")
    def notebook_current():
        return jsonify(notebook.current())

    @app.get("/notebook-api/today")
    def notebook_today():
        return jsonify(notebook.today())

    @app.get("/notebook-api/week/<label>")
    def notebook_week(label):
        if not iso_week.is_label(label):
            return jsonify(error=notebook_text.ERRORS["bad_week"]), 400
        return jsonify(notebook.week(psid, label))

    @app.get("/notebook-api/weeks")
    def notebook_weeks():
        return jsonify(views.WeeksView(weeks=notebook.weeks(psid)))

    @app.get("/notebook-api/injury")
    def notebook_injury():
        zone = request.args.get("zone") or None
        if zone is not None and not ZONE.fullmatch(zone):
            return jsonify(error=notebook_text.ERRORS["bad_zone"]), 400
        view = notebook.injury(zone)
        if view is None:
            return jsonify(error=notebook_text.ERRORS["no_zone"]), 404
        return jsonify(view)

    @app.get("/notebook-api/session/<observation_id>")
    def notebook_session(observation_id):
        if not UUID.fullmatch(observation_id):
            return jsonify(error=notebook_text.ERRORS["bad_session"]), 400
        view = notebook.session(observation_id)
        if view is None:
            return jsonify(error=notebook_text.ERRORS["no_session"]), 404
        return jsonify(view)

    @app.get("/notebook-api/race")
    def notebook_race():
        return jsonify(notebook.race(psid))

    @app.get("/notebook-api/race-plan")
    def notebook_race_plan():
        try:
            view = notebook.race_plan()
        except race_plan.Pending:
            return jsonify(error=notebook_text.ERRORS["race_plan_pending"]), 503
        if view is None:
            return jsonify(error=notebook_text.ERRORS["no_race_plan"]), 404
        return jsonify(view)

    @app.get("/notebook-api/weather")
    def notebook_weather():
        day, start = request.args.get("date", ""), request.args.get("time") or None
        where = (request.args.get("place") or "").strip() or place
        if not DAY.fullmatch(day) or (start and not CLOCK.fullmatch(start)) \
                or not PLACE.fullmatch(where):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            when = dt.date.fromisoformat(day)
        except ValueError:
            return jsonify(error=notebook_text.ERRORS["bad_date"]), 400
        # No forecast is `null`: the session is too far off, the place unknown, the
        # network down.
        return jsonify(weather.forecast(where, when, start, clock.now()))

    @app.post("/notebook-api/reorganize")
    def notebook_reorganize():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"week", "days"}
                or not isinstance(body["week"], str) or not iso_week.is_label(body["week"])):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            return jsonify(notebook.reorganize(body["week"], body["days"]))
        except SchemaError:
            return jsonify(error=notebook_text.ERRORS["bad_days"]), 400

    @app.post("/notebook-api/reorganize/take")
    def notebook_take_variant():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"week", "days", "revision", "choice"}
                or not isinstance(body["week"], str) or not iso_week.is_label(body["week"])
                or type(body["revision"]) is not int or type(body["choice"]) is not int):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            number = notebook.take_variant(body["week"], body["days"], body["revision"],
                                           body["choice"])
        except reorganize.ReorganizeError:
            return jsonify(error=notebook_text.ERRORS["week_moved"]), 409
        except SchemaError:
            return jsonify(error=notebook_text.ERRORS["bad_days"]), 400
        return jsonify(views.Revised(status="ok", revision=number))

    @app.post("/notebook-api/days")
    def notebook_state_days():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"week", "days"}
                or not isinstance(body["week"], str) or not iso_week.is_label(body["week"])):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            notebook.state_days(body["week"], body["days"])
        except SchemaError:
            return jsonify(error=notebook_text.ERRORS["bad_days"]), 400
        except reorganize.ReorganizeError:
            return jsonify(error=notebook_text.ERRORS["week_over"]), 409
        return jsonify(views.Done(status="ok"))

    @app.get("/notebook-api/habits")
    def notebook_habits():
        return jsonify(notebook.habits())

    @app.post("/notebook-api/habits")
    def notebook_add_habit():
        body = request.get_json(silent=True)
        try:
            notebook.add_habit(body)
        except SchemaError:
            return jsonify(error=notebook_text.ERRORS["bad_habit"]), 400
        return jsonify(views.Done(status="ok"))

    @app.post("/notebook-api/habits/end")
    def notebook_end_habit():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"id"} or not isinstance(body["id"], str)
                or not UUID.fullmatch(body["id"])):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            notebook.end_habit(body["id"])
        except reorganize.ReorganizeError:
            return jsonify(error=notebook_text.ERRORS["no_habit"]), 404
        return jsonify(views.Done(status="ok"))

    @app.post("/notebook-api/reorganize/ask")
    def notebook_ask_coach():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"week", "days", "wish", "context"}
                or not isinstance(body["week"], str) or not iso_week.is_label(body["week"])
                or not isinstance(body["wish"], str) or not isinstance(body["context"], str)):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            text = notebook.ask_coach(body["week"], body["days"], body["wish"], body["context"])
        except SchemaError:
            return jsonify(error=notebook_text.ERRORS["bad_days"]), 400
        except reorganize.ReorganizeError:
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            alert(text, f"notebook:reorganize:{body['week']}:{int(clock.now().timestamp())}")
        except Exception:
            app.logger.exception("Reorganisation request not passed to the coach")
            return jsonify(error=notebook_text.ERRORS["coach_unreachable"]), 503
        return jsonify(views.Done(status="ok"))

    @app.post("/notebook-api/proposals/take")
    def notebook_take_proposal():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"id"} or not isinstance(body["id"], str)
                or not UUID.fullmatch(body["id"])):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        try:
            number = notebook.take_proposal(body["id"])
        except (reorganize.ReorganizeError, SchemaError, StorageError):
            return jsonify(error=notebook_text.ERRORS["proposal_gone"]), 409
        return jsonify(views.Revised(status="ok", revision=number))

    @app.post("/notebook-api/proposals/dismiss")
    def notebook_dismiss_proposals():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"week"}
                or not isinstance(body["week"], str) or not iso_week.is_label(body["week"])):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        notebook.dismiss_proposals(body["week"])
        return jsonify(views.Done(status="ok"))

    @app.post("/notebook-api/answer")
    def notebook_answer():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"ask", "value"}
                or not isinstance(body["ask"], str) or not ASK.fullmatch(body["ask"])):
            return jsonify(error=notebook_text.ERRORS["bad_answer"]), 400
        try:
            result = notebook.board().answer(body["ask"], body["value"])
        except AnswerError as error:
            return jsonify(error=str(error)), 409
        if result["alert"]:
            try:
                alert(result["alert"], "notebook:" + body["ask"])
            except Exception:
                app.logger.exception("Notebook alert not passed to the coach")
        if result.get("activity"):
            try:
                settled(int(result["activity"]))
            except Exception:
                app.logger.exception("The run's debrief was not brought forward")
        answered: views.Answered = {"status": "ok", "alert": bool(result["alert"])}
        return jsonify(answered)

    @app.post("/notebook-api/launch")
    def notebook_launch():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"signature"} or not isinstance(body["signature"], str):
            return jsonify(error=notebook_text.ERRORS["bad_request"]), 400
        url = notebook.launch(psid, body["signature"])
        if not url:
            return jsonify(error=notebook_text.ERRORS["session_changed"]), 409
        launched: views.Launched = {"url": url}
        return jsonify(launched)

    @app.get("/notebook-api/entries/<entry_id>")
    def notebook_entry(entry_id):
        entry = notebook.store.get(psid, entry_id)
        if entry is None:
            return jsonify(error=notebook_text.ERRORS["no_card"]), 404
        snapshot = entry["snapshot"]
        view: views.EntryView = {
            "id": entry["id"], "kind": entry["kind"], "title": entry["title"],
            "period": entry["period"], "occurred_on": entry["occurred_on"],
            "created_at": entry["created_at"], "origin": entry["origin"], "body": entry["body"],
            "image_hash": entry["image_hash"], "snapshot": snapshot,
            "report": (notebook.store.strength_report(snapshot["sid"])
                       if snapshot and snapshot.get("sid") else None),
            "related": (notebook.store.list_entries(psid, kind="weekly", month=entry["period"],
                                                    limit=100)
                        if entry["kind"] == "monthly" and entry["period"] else []),
        }
        return jsonify(view)

    @app.get("/notebook-api/entries/<entry_id>/image")
    def notebook_image(entry_id):
        entry = notebook.store.get(psid, entry_id, image=True)
        if entry is None or entry["image"] is None:
            return "not found", 404
        response = app.response_class(bytes(entry["image"]), mimetype=entry["image_type"])
        response.headers["Content-Disposition"] = f'inline; filename="redline-{entry_id}.png"'
        return response
