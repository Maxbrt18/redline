"""Notebook: document archives and access to the canonical strength session of the day."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from redline.cards import archive, strength_card
from redline.coach.fr import notebook as notebook_text
from redline.model import notebook_views as views
from redline.model import race as race_model
from redline.model.instance import Instance
from redline.model.program import load
from redline.storage.database import StorageError
from redline.storage.notebook import NotebookStore

from . import clock, race_plan, session_detail, strength
from .notebook_board import Board, week_monday
from .plans import PlanStatus, blocked_session_ids, strength_due
from .strength_artifact import selection_for

if TYPE_CHECKING:
    from redline.storage.repository import OperationalStore

    from .delivery import OutboundEnvelope
    from .state import StateStore
    from .vault import VaultRepo


def review_period(source_key: str) -> tuple[str, str]:
    """The monthly key names the trigger on the 1st, hence closes the previous month."""
    match = re.fullmatch(r"proactive:weekly:(20\d{2}-W\d{2})", source_key)
    if match:
        return "weekly", match[1]
    match = re.fullmatch(r"proactive:monthly:(20\d{2}-\d{2})", source_key)
    if match:
        first = dt.date.fromisoformat(match[1] + "-01")
        return "monthly", (first - dt.timedelta(days=1)).strftime("%Y-%m")
    return "other", ""


def period_day(kind: str, period: str, fallback: dt.date) -> dt.date:
    if not period:
        return fallback
    if kind == "weekly" and re.fullmatch(r"20\d{2}-W\d{2}", period):
        return dt.date.fromisocalendar(int(period[:4]), int(period[-2:]), 7)
    if kind == "monthly" and re.fullmatch(r"20\d{2}-\d{2}", period):
        return dt.date.fromisoformat(period + "-01")
    if kind == "strength":
        return dt.date.fromisoformat(period)
    raise ValueError("invalid notebook period")


class NotebookService:
    def __init__(self, store: NotebookStore, repo: VaultRepo, state: StateStore,
                 root: Path, base_url: str, operational: OperationalStore | None = None,
                 instance: Instance | None = None):
        self.store, self.repo, self.state = store, repo, state
        self.instance = instance
        self.root, self.base_url = root, base_url.rstrip("/")
        self.operational = operational

    def board(self) -> Board:
        if self.operational is None:
            raise StorageError("ledger unavailable for the notebook")
        return Board(self.operational, self.repo, self.root, clock.today(), clock.now(),
                     self.instance)

    def today(self) -> views.TodayView:
        return {**self.board().today_view(), "strength": self.current()}

    def week(self, psid: str, label: str) -> views.WeekView:
        monday = week_monday(label)
        entries = self.store.list_entries(
            psid, limit=100, between=(monday, monday + dt.timedelta(days=6)))
        return self.board().week_view(label, entries)

    def weeks(self, psid: str) -> list[views.PastWeek]:
        reviews = {entry["period"]: entry["id"] for entry in
                   self.store.list_entries(psid, kind="weekly", limit=100) if entry.get("period")}
        return self.board().weeks_view(reviews)

    def injury(self, zone: str | None = None) -> views.InjuryView | None:
        return self.board().injury_view(zone)

    def session(self, observation_id: str) -> views.SessionView | None:
        """The detail of a completed session; streams and weather are fetched on first display."""
        board = self.board()
        intake = board.ops.strava_detail(uuid.UUID(observation_id))
        streams = (intake or {}).get("streams")
        if intake is not None and (streams is None or "lap_times" not in streams
                                   or intake.get("weather") is None):
            intake = session_detail.ensure(board.ops, intake)
        return board.session_view(observation_id, intake)

    def race(self, psid: str) -> views.RaceView:
        archives = [*self.store.list_entries(psid, kind="monthly", limit=12),
                    *self.store.list_entries(psid, kind="weekly", limit=8)]
        return self.board().race_view(archives)

    def race_plan(self) -> views.RacePlanView | None:
        """The plan screen of the typed race; None when it declares no readable route."""
        board = self.board()
        return race_plan.view(race_model.upcoming(board.ops, board.today), board.root,
                              board.instance, board.today)

    def connected(self, psid: str) -> bool:
        """Does the notebook collect the feelings? Unreadable database: no, Messenger
        then takes its role back rather than leaving the cells without a requester."""
        try:
            return self.store.has_device(psid)
        except Exception:
            return False

    def wake_reminder_due(self) -> bool:
        """The notebook still awaits the wake trace."""
        try:
            return any(ask["type"] == "wake" for ask in self.board().asks())
        except Exception:
            return False

    def link(self, psid: str, entry: str = "") -> str:
        token = self.store.issue(psid)
        return f"{self.base_url}/carnet#t={token}" + (f"&entry={entry}" if entry else "")

    def capture(self, envelope: OutboundEnvelope) -> list[str]:
        from .delivery import ImagePart, NotebookButtonPart, TextPart

        existing = self.store.source_entries(envelope.psid, envelope.source_key)
        if existing:
            return existing
        kind, period = review_period(envelope.source_key)
        body = "\n\n".join(part.text for part in envelope.parts
                           if isinstance(part, TextPart | NotebookButtonPart))
        records = []
        has_review = False
        for part in envelope.parts:
            if not isinstance(part, ImagePart):
                continue
            metadata = archive.read(part.path)
            snapshot = part.link[1] if part.link else None
            image_kind = "strength" if snapshot else metadata.get("kind", "other")
            image_period = str(snapshot.get("date") or "") if snapshot else metadata.get("period", "")
            # The job is the authority on the period even if the agent gave a wrong title.
            if image_kind == kind and kind in {"weekly", "monthly"}:
                image_period = period
            title = (snapshot.get("title") if snapshot else metadata.get("title")) or notebook_text.CARD_TITLE
            if image_kind == "weekly" and image_period:
                title = f"Weekly · {image_period}"
            if image_kind == "monthly" and image_period:
                title = f"Monthly · {image_period}"
            has_review |= image_kind == kind
            data = part.path.read_bytes()
            if len(data) > 12 * 1024 * 1024:
                raise ValueError("card too large for the notebook")
            records.append({"slot": part.path.name,
                "kind": image_kind, "title": title, "period": image_period,
                "occurred_on": period_day(image_kind, image_period, clock.today()),
                "body": body, "image": data, "image_hash": hashlib.sha256(data).hexdigest(),
                "image_type": "image/jpeg" if part.path.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
                "snapshot": snapshot})
        if kind in {"weekly", "monthly"} and not has_review:
            records.insert(0, {"slot": "review", "kind": kind,
                "title": f"{'Weekly' if kind == 'weekly' else 'Monthly'} · {period}",
                "period": period, "occurred_on": period_day(kind, period, clock.today()), "body": body})
        return self.store.save_many(envelope.psid, envelope.source_key, records)

    def current(self) -> views.StrengthOffer:
        today = clock.today()
        week = today.strftime("%G-W%V")
        status = self.repo.validate_weekly_plan(week)
        if status not in {PlanStatus.VALID, PlanStatus.AWAITING}:
            return {"status": "unavailable", "date": today.isoformat(),
                    "message": notebook_text.STRENGTH_PLAN_UNCHECKED}
        raw = self.repo.weekly_plan_context(week)
        if not raw:
            return {"status": "unavailable", "date": today.isoformat(),
                    "message": notebook_text.STRENGTH_PLAN_MISSING}
        session = strength_due(raw, today.isoformat())
        if session is None:
            return {"status": "none", "date": today.isoformat(),
                    "message": notebook_text.STRENGTH_NONE}
        if session["id"] in blocked_session_ids(raw, today.isoformat()):
            return {"status": "blocked", "date": today.isoformat(),
                    "message": notebook_text.STRENGTH_BLOCKED}
        built = load(self.root).build(selection_for(session, today.isoformat()))
        snapshot = strength_card.snapshot(built, root=self.root)
        # The notebook does not offer to redo an already recorded session, even if the
        # program matching has not yet finished its coaching turn.
        import sqlalchemy as sa

        from redline.storage.schema import strength_report_claims
        with self.store.database.engine.connect() as connection:
            done = connection.execute(sa.select(strength_report_claims.c.session_key).where(
                strength_report_claims.c.session_key == snapshot["sid"])).scalar_one_or_none()
        if done:
            return {"status": "done", "date": today.isoformat(),
                    "message": notebook_text.STRENGTH_DONE}
        revision = json.loads(raw)["revision"]
        canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
        signature = hashlib.sha256(f"{revision}:{canonical}".encode()).hexdigest()
        return {"status": "ready", "date": today.isoformat(), "session": snapshot,
                "revision": revision, "signature": signature}

    def launch(self, psid: str, signature: str) -> str | None:
        current = self.current()
        if current["status"] != "ready" or current["signature"] != signature:
            return None
        token = self.state.issue_strength_link(psid, current["session"])
        return strength.token_url(self.base_url, token)
