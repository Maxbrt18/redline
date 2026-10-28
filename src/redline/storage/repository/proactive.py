"""Proactive messages that were sent."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from ..database import StorageError
from ..schema import (
    proactive_messages,
)
from ._common import StoreBase, _audit, _jsonable, _uuid


class ProactiveMessages(StoreBase):
    def add_proactive_message_in(self, connection: Connection, row: dict,
                                 *, actor: str = "application") -> uuid.UUID:
        message_id = _uuid(row.get("id"))
        values = {
            "id": message_id, "athlete_id": self.athlete_id,
            "source_key": row["source_key"], "sent_at": row["sent_at"],
            "kind": row["kind"], "angle": row.get("angle"),
            "summary": row["summary"],
        }
        inserted = connection.execute(pg_insert(proactive_messages).values(
            **values).on_conflict_do_nothing(
                constraint="uq_proactive_messages_source_key"
            ).returning(proactive_messages.c.id)).scalar_one_or_none()
        if inserted is not None:
            _audit(connection, actor, "proactive_message.added", "proactive_message",
                   inserted, _jsonable(values))
            return inserted
        existing = connection.execute(sa.select(proactive_messages).where(
            proactive_messages.c.athlete_id == self.athlete_id,
            proactive_messages.c.source_key == row["source_key"],
        )).mappings().one()
        comparable = ("sent_at", "kind", "angle", "summary")
        if any(existing[key] != values[key] for key in comparable):
            raise StorageError(
                f"collision d'idempotence pour le message {row['source_key']!r}")
        return existing["id"]

    def add_proactive_message(self, row: dict, *, actor: str = "application") -> uuid.UUID:
        with self.database.transaction() as connection:
            return self.add_proactive_message_in(connection, row, actor=actor)
    def proactive_message_rows(self, *, limit: int = 10) -> list[dict]:
        if limit < 1:
            raise ValueError("limit doit être positif")
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(
                proactive_messages).where(
                    proactive_messages.c.athlete_id == self.athlete_id
                ).order_by(proactive_messages.c.sent_at.desc()).limit(limit)).mappings()]
