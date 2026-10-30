"""Immutable notebook archives and private capabilities, backed up with PostgreSQL."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .database import Database
from .schema import notebook_credentials as credentials
from .schema import notebook_entries as entries
from .schema import (
    outbound_deliveries,
    strength_pain_readings,
    strength_results,
    strength_sessions,
    strength_sets,
)

DEVICE_SECONDS = 365 * 24 * 3600
LINK_SECONDS = 24 * 3600


def token_hash(token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return ""
    return hashlib.sha256(token.encode()).hexdigest()


class NotebookStore:
    def __init__(self, database: Database):
        self.database = database

    def issue(self, psid: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.database.transaction() as connection:
            connection.execute(credentials.insert().values(
                token_hash=token_hash(token), psid=psid, purpose="link",
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=LINK_SECONDS)))
        return token

    def exchange(self, psid: str, token: str) -> str | None:
        now = dt.datetime.now(dt.UTC)
        device = secrets.token_urlsafe(32)
        with self.database.transaction() as connection:
            consumed = connection.execute(credentials.update().where(
                credentials.c.token_hash == token_hash(token), credentials.c.psid == psid,
                credentials.c.purpose == "link", credentials.c.revoked_at.is_(None),
                credentials.c.expires_at > now,
            ).values(revoked_at=now).returning(credentials.c.token_hash)).scalar_one_or_none()
            if consumed is None:
                return None
            connection.execute(credentials.insert().values(
                token_hash=token_hash(device), psid=psid, purpose="device",
                expires_at=now + dt.timedelta(seconds=DEVICE_SECONDS)))
        return device

    def authenticate(self, psid: str, token: str) -> bool:
        if not token_hash(token):
            return False
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(credentials.c.token_hash).where(
                credentials.c.token_hash == token_hash(token), credentials.c.psid == psid,
                credentials.c.purpose == "device", credentials.c.revoked_at.is_(None),
                credentials.c.expires_at > dt.datetime.now(dt.UTC),
            )).scalar_one_or_none() is not None

    def has_device(self, psid: str) -> bool:
        """A connected, valid device exists: the notebook can collect the athlete's feedback."""
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(credentials.c.token_hash).where(
                credentials.c.psid == psid, credentials.c.purpose == "device",
                credentials.c.revoked_at.is_(None),
                credentials.c.expires_at > dt.datetime.now(dt.UTC),
            ).limit(1)).scalar_one_or_none() is not None

    def revoke(self, psid: str, token: str | None = None) -> None:
        with self.database.transaction() as connection:
            query = credentials.update().where(credentials.c.psid == psid,
                                                credentials.c.revoked_at.is_(None))
            if token is not None:
                query = query.where(credentials.c.token_hash == token_hash(token))
            connection.execute(query.values(revoked_at=dt.datetime.now(dt.UTC)))

    def source_entries(self, psid: str, source_key: str) -> list[str]:
        with self.database.engine.connect() as connection:
            return [str(value) for value in connection.execute(sa.select(entries.c.id).where(
                entries.c.psid == psid, entries.c.source_key == source_key,
            ).order_by(entries.c.slot)).scalars()]

    def save_many(self, psid: str, source_key: str, records: list[dict]) -> list[str]:
        """The whole batch is stored before sending; no partially saved prefix."""
        ids = []
        with self.database.transaction() as connection:
            for values in records:
                identity = uuid.uuid5(uuid.NAMESPACE_URL, f"redline-notebook:{psid}:{source_key}:{values['slot']}")
                connection.execute(pg_insert(entries).values(
                    id=identity, psid=psid, source_key=source_key, **values,
                ).on_conflict_do_nothing(index_elements=[entries.c.psid, entries.c.source_key,
                                                         entries.c.slot]))
                ids.append(str(identity))
        return ids

    @staticmethod
    def _visible():
        # A prepared artifact whose reply was never sent stays internal.
        return sa.or_(entries.c.origin == "recovered", sa.exists(sa.select(1).where(
            outbound_deliveries.c.source_key == entries.c.source_key,
            outbound_deliveries.c.psid == entries.c.psid,
            outbound_deliveries.c.status == "delivered")))

    def list_entries(self, psid: str, *, offset: int = 0, limit: int = 40,
                     kind: str = "", month: str = "",
                     between: tuple[dt.date, dt.date] | None = None) -> list[dict]:
        filters = [entries.c.psid == psid, self._visible()]
        if kind:
            filters.append(entries.c.kind == kind)
        if between:
            filters.extend([entries.c.occurred_on >= between[0],
                            entries.c.occurred_on <= between[1]])
        if month:
            first = dt.date.fromisoformat(month + "-01")
            following = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            filters.extend([entries.c.occurred_on >= first, entries.c.occurred_on < following])
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(
                entries.c.id, entries.c.kind, entries.c.title, entries.c.period,
                entries.c.occurred_on, entries.c.created_at, entries.c.origin,
                entries.c.image.is_not(None).label("has_image"),
            ).where(*filters).order_by(
                entries.c.occurred_on.desc(), entries.c.created_at.desc(), entries.c.id
            ).offset(offset).limit(limit)).mappings()
            return [self._public(dict(row)) for row in rows]

    @staticmethod
    def _public(row: dict) -> dict:
        for key in ("id", "occurred_on", "created_at"):
            if key in row:
                row[key] = str(row[key])
        return row

    def get(self, psid: str, entry_id: str, *, image: bool = False) -> dict | None:
        try:
            identity = uuid.UUID(entry_id)
        except ValueError:
            return None
        columns = ([entries.c.image, entries.c.image_type] if image else
                   [column for column in entries.c if column.name not in {"image", "psid"}])
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(*columns).where(
                entries.c.id == identity, entries.c.psid == psid, self._visible(),
            )).mappings().one_or_none()
        return self._public(dict(row)) if row else None

    def strength_report(self, session_key: str) -> dict | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(strength_sessions).where(
                strength_sessions.c.session_key == session_key)).mappings().one_or_none()
            if row is None:
                return None
            result: dict[str, Any] = {
                key: str(row[key]) if row[key] is not None else None
                for key in ("performed_on", "duration_minutes", "note")}
            pains: dict[str, dict[str, str | None]] = {}
            for reading in connection.execute(sa.select(strength_pain_readings).where(
                    strength_pain_readings.c.strength_session_id == row["id"])).mappings():
                pains.setdefault(reading["zone"], {"before": None, "after": None})[
                    reading["moment"]] = str(reading["value"])
            result["pains"] = pains
            exercises = []
            for exercise in connection.execute(sa.select(strength_results).where(
                strength_results.c.session_id == row["id"]).order_by(strength_results.c.position)).mappings():
                sets = [dict(item) for item in connection.execute(sa.select(
                    strength_sets.c.side, strength_sets.c.set_index, strength_sets.c.repetitions,
                ).where(strength_sets.c.result_id == exercise["id"]).order_by(
                    strength_sets.c.side, strength_sets.c.set_index)).mappings()]
                exercises.append({"slug": exercise["exercise_slug"], "status": exercise["status"],
                                  "note": exercise["note"], "difficulty": exercise["difficulty"],
                                  "completed_slots": exercise["completed_slots"], "sets": sets})
            return {**result, "exercises": exercises}
