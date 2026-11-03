"""Revisions the coach proposes for a week, kept until the athlete takes one."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from ..database import StorageError
from ..schema import plan_proposals
from ._common import _audit, _jsonable
from .plans import Plans


class Proposals(Plans):
    def add_proposals(self, week: str, based_on: int, proposals: list[dict],
                      *, actor: str = "application") -> list[uuid.UUID]:
        with self.database.transaction() as connection:
            return self.add_proposals_in(connection, week, based_on, proposals, actor=actor)

    def add_proposals_in(self, connection: Connection, week: str, based_on: int,
                         proposals: list[dict], *, actor: str = "application") -> list[uuid.UUID]:
        """The new set replaces the proposals still open for the week."""
        self._settle_in(connection, week, "superseded")
        ids = []
        for position, proposal in enumerate(proposals, 1):
            proposal_id = uuid.uuid4()
            connection.execute(plan_proposals.insert().values(
                id=proposal_id, athlete_id=self.athlete_id, week=week, based_on=based_on,
                position=position, title=proposal["title"], body=proposal["body"],
                revision=_jsonable(proposal["revision"])))
            ids.append(proposal_id)
        _audit(connection, actor, "proposals.added", "plan", uuid.uuid5(
            uuid.NAMESPACE_URL, f"proposals:{self.athlete_id}:{week}:{based_on}"),
            {"week": week, "based_on": based_on, "ids": [str(item) for item in ids]})
        return ids

    def open_proposals(self, week: str) -> list[dict]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(plan_proposals).where(
                plan_proposals.c.athlete_id == self.athlete_id,
                plan_proposals.c.week == week,
                plan_proposals.c.outcome.is_(None)).order_by(plan_proposals.c.position)).mappings()
            return [dict(row) for row in rows]

    def open_proposal(self, proposal_id: uuid.UUID) -> dict | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(plan_proposals).where(
                plan_proposals.c.id == proposal_id,
                plan_proposals.c.athlete_id == self.athlete_id,
                plan_proposals.c.outcome.is_(None))).mappings().first()
            return dict(row) if row else None

    def take_proposal(self, proposal_id: uuid.UUID, revision: dict,
                      *, actor: str = "notebook") -> dict:
        """Write `revision` (the proposal, stamped and checked) and settle the week's set."""
        with self.database.transaction() as connection:
            row = connection.execute(sa.select(plan_proposals).where(
                plan_proposals.c.id == proposal_id,
                plan_proposals.c.athlete_id == self.athlete_id,
                plan_proposals.c.outcome.is_(None)).with_for_update()).mappings().first()
            if row is None:
                raise StorageError(f"proposal {proposal_id}: unknown or settled")
            saved = self.append_plan_revision_in(connection, row["week"], revision, actor=actor)
            self._settle_in(connection, row["week"], "dismissed", taken=proposal_id)
            return saved

    def dismiss_proposals(self, week: str, *, actor: str = "notebook") -> None:
        with self.database.transaction() as connection:
            self._settle_in(connection, week, "dismissed")

    def _settle_in(self, connection: Connection, week: str, outcome: str,
                   taken: uuid.UUID | None = None) -> None:
        now = sa.func.clock_timestamp()
        open_rows = sa.and_(plan_proposals.c.athlete_id == self.athlete_id,
                            plan_proposals.c.week == week, plan_proposals.c.outcome.is_(None))
        if taken is not None:
            connection.execute(plan_proposals.update().where(
                open_rows, plan_proposals.c.id == taken).values(outcome="taken", settled_at=now))
        connection.execute(plan_proposals.update().where(open_rows)
                           .values(outcome=outcome, settled_at=now))
