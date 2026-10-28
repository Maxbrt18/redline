"""Questions asked to the athlete and search across the exchanges."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa

from ..schema import (
    conversation_messages,
    conversation_questions,
    turn_scopes,
)
from ._common import StoreBase


class Conversation(StoreBase):
    def review_question(self, payload: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self._apply_question_review(connection, payload, actor)

    def question_rows(self, limit: int = 20) -> list[dict]:
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(conversation_questions).where(
                conversation_questions.c.status.in_(("open", "deferred"))).order_by(
                    conversation_questions.c.asked_at.desc()).limit(limit)).mappings()]

    def conversation_search(self, psid: str, query: str, limit: int = 6) -> list[dict]:
        """Search past messages, with provenance and the immediately adjacent messages."""
        if not psid or not 3 <= len(query.strip()) <= 160 or not 1 <= limit <= 12:
            raise ValueError("psid, recherche de 3 à 160 caractères et limite 1 à 12 attendus")
        french: Any = sa.literal_column("'french'::regconfig")
        document = sa.func.to_tsvector(french, conversation_messages.c.body)
        search = sa.func.websearch_to_tsquery(french, query)
        rank = sa.func.ts_rank_cd(document, search)
        with self.database.engine.connect() as connection:
            hits = list(connection.execute(sa.select(
                conversation_messages.c.id, conversation_messages.c.role,
                conversation_messages.c.body, conversation_messages.c.created_at,
                rank.label("rank"),
            ).where(conversation_messages.c.psid == psid,
                    document.op("@@")(search)).order_by(
                        rank.desc(), conversation_messages.c.created_at.desc()).limit(limit)
            ).mappings())
            results = []
            for hit in hits:
                before = connection.execute(sa.select(
                    conversation_messages.c.role, conversation_messages.c.body,
                    conversation_messages.c.created_at,
                ).where(conversation_messages.c.psid == psid,
                        conversation_messages.c.created_at < hit["created_at"]
                ).order_by(conversation_messages.c.created_at.desc()).limit(1)).mappings().first()
                after = connection.execute(sa.select(
                    conversation_messages.c.role, conversation_messages.c.body,
                    conversation_messages.c.created_at,
                ).where(conversation_messages.c.psid == psid,
                        conversation_messages.c.created_at > hit["created_at"]
                ).order_by(conversation_messages.c.created_at).limit(1)).mappings().first()
                results.append({"hit": dict(hit), "before": dict(before) if before else None,
                                "after": dict(after) if after else None})
            return results

    def conversation_search_for_turn(self, turn_id: str, query: str,
                                     limit: int = 6) -> list[dict]:
        """The only history scope exposed to the agent CLI."""
        try:
            identifier = uuid.UUID(turn_id)
        except (ValueError, TypeError) as exc:
            raise ValueError("identifiant de tour invalide") from exc
        with self.database.engine.connect() as connection:
            psid = connection.execute(sa.select(turn_scopes.c.psid).where(
                turn_scopes.c.turn_id == identifier,
                turn_scopes.c.created_at >= dt.datetime.now(dt.UTC) - dt.timedelta(hours=4),
            )).scalar_one_or_none()
        if psid is None:
            raise ValueError("tour absent ou expiré")
        return self.conversation_search(psid, query, limit)
