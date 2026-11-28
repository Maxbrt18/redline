"""The versions of the training cycles: the active one, the transitions that replace it,
and the preparation proposed to the athlete until they take it.

A version is written whole, trame included, in one transaction, and the one it replaces
is settled in the same transaction: there is never a moment with two cycles in force.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from redline.model import cycles as cycle_model
from redline.model.cycles import CycleVersion
from redline.model.plan_generator import Trame
from redline.model.vocabulary import (
    CycleCause,
    CycleKind,
    CycleVersionState,
    GeneralLevel,
    PlanPhase,
    ProactiveKind,
)

from ..database import StorageError
from ..schema import (
    cycle_key_sessions,
    cycle_versions,
    cycle_weeks,
    deadlines,
    proactive_jobs,
)
from ._common import StoreBase, _audit, _jsonable, _native, _uuid

#: The kinds of cycle whose weeks are planned low: they are not a start.
LOW_KINDS = (CycleKind.RECOVERY, CycleKind.REBUILD)
#: The causes that write a new version of the cycle in force rather than a new cycle.
SAME_CYCLE = (CycleCause.BLOCK_END, CycleCause.LEVEL_CHANGE)


class Cycles(StoreBase):

    def _versions_in(self, connection: Connection, *conditions) -> list[CycleVersion]:
        rows = [dict(row) for row in connection.execute(
            sa.select(cycle_versions, deadlines.c.deadline_key.label("race_key")).outerjoin(
                deadlines, deadlines.c.id == cycle_versions.c.race_id).where(
                cycle_versions.c.athlete_id == self.athlete_id, *conditions,
            ).order_by(cycle_versions.c.created_at, cycle_versions.c.version)).mappings()]
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        weeks: dict[uuid.UUID, list[dict]] = {key: [] for key in ids}
        for row in connection.execute(sa.select(cycle_weeks).where(
                cycle_weeks.c.cycle_version_id.in_(ids))).mappings():
            weeks[row["cycle_version_id"]].append(_native(row))
        keys: dict[uuid.UUID, list[dict]] = {key: [] for key in ids}
        for row in connection.execute(sa.select(cycle_key_sessions).where(
                cycle_key_sessions.c.cycle_version_id.in_(ids))).mappings():
            keys[row["cycle_version_id"]].append(_native(row))
        return [cycle_model.from_rows(row, weeks[row["id"]], keys[row["id"]]) for row in rows]

    def active_cycle_in(self, connection: Connection) -> CycleVersion | None:
        found = self._versions_in(connection,
                                  cycle_versions.c.state == CycleVersionState.ACTIVE)
        return found[0] if found else None

    def active_cycle(self) -> CycleVersion | None:
        """The version in force, None before the first."""
        with self.database.engine.connect() as connection:
            return self.active_cycle_in(connection)

    def cycle_version(self, version_id: uuid.UUID) -> CycleVersion | None:
        with self.database.engine.connect() as connection:
            found = self._versions_in(connection, cycle_versions.c.id == version_id)
        return found[0] if found else None

    def cycle_for_week(self, week: str) -> CycleVersion | None:
        """The version that holds `week`: of the taken versions whose trame has it, the
        latest written."""
        holding = sa.exists().where(cycle_weeks.c.cycle_version_id == cycle_versions.c.id,
                                    cycle_weeks.c.iso_week == week)
        with self.database.engine.connect() as connection:
            found = self._versions_in(connection, cycle_versions.c.taken_at.is_not(None),
                                      holding)
        return found[-1] if found else None

    def first_cycle_week(self) -> str | None:
        """The first week a taken version holds; the weeks before it have no version."""
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(sa.func.min(cycle_weeks.c.iso_week)).join(
                cycle_versions, cycle_versions.c.id == cycle_weeks.c.cycle_version_id).where(
                cycle_versions.c.athlete_id == self.athlete_id,
                cycle_versions.c.taken_at.is_not(None))).scalar_one_or_none()

    def cycle_decision(self, version_id: uuid.UUID) -> dict | None:
        """The typed decision a version was written with."""
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(cycle_versions.c.decision).where(
                cycle_versions.c.athlete_id == self.athlete_id,
                cycle_versions.c.id == version_id)).scalar_one_or_none()

    def has_cycle_in(self, connection: Connection) -> bool:
        """Whether the instance ever had a version."""
        return connection.execute(sa.select(sa.exists().where(
            cycle_versions.c.athlete_id == self.athlete_id))).scalar_one()

    def planned_low_weeks_in(self, connection: Connection) -> set[str]:
        """The weeks a taken version planned low: its tapers, its deloads, its recoveries
        and its rebuilds."""
        return set(connection.execute(sa.select(cycle_weeks.c.iso_week).join(
            cycle_versions, cycle_versions.c.id == cycle_weeks.c.cycle_version_id).where(
            cycle_versions.c.athlete_id == self.athlete_id,
            cycle_versions.c.taken_at.is_not(None),
            sa.or_(cycle_versions.c.kind.in_(LOW_KINDS), cycle_weeks.c.deload.is_(True),
                   cycle_weeks.c.phase == PlanPhase.TAPER))).scalars())

    def _race_id_in(self, connection: Connection, race_key: str | None) -> uuid.UUID | None:
        if not race_key:
            return None
        return connection.execute(sa.select(deadlines.c.id).where(
            deadlines.c.athlete_id == self.athlete_id,
            deadlines.c.deadline_key == race_key)).scalar_one_or_none()

    def _insert_in(self, connection: Connection, trame: Trame, values: dict) -> CycleVersion:
        """A version and its trame, written whole."""
        version_id = _uuid(None)
        row = {"id": version_id, "athlete_id": self.athlete_id, **values,
               **cycle_model.version_values(trame)}
        connection.execute(cycle_versions.insert().values(**row))
        connection.execute(cycle_weeks.insert(), _with(version_id,
                                                       cycle_model.week_values(trame)))
        keys = cycle_model.key_values(trame)
        if keys:
            connection.execute(cycle_key_sessions.insert(),
                               [{**key, "id": _uuid(None)} for key in _with(version_id, keys)])
        return self._versions_in(connection, cycle_versions.c.id == version_id)[0]

    @staticmethod
    def _lock_in(connection: Connection) -> None:
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('redline:cycle'))"))

    def apply_transition_in(self, connection: Connection, trame: Trame, cause: CycleCause, *,
                            level: GeneralLevel | None = None, race_key: str | None = None,
                            actor: str = "application") -> CycleVersion:
        """The next cycle, in force at once: the next block or a new level of a general
        preparation is a new version of its cycle, any other transition a new cycle. The
        active version is superseded or closed in the same transaction."""
        self._lock_in(connection)
        now = dt.datetime.now(dt.UTC)
        active = self.active_cycle_in(connection)
        same = active is not None and cause in SAME_CYCLE
        if active is not None:
            connection.execute(cycle_versions.update().where(
                cycle_versions.c.id == active.id).values(
                state=CycleVersionState.SUPERSEDED if same else CycleVersionState.CLOSED,
                settled_at=now))
        values = {"cycle_key": active.cycle_key if same and active else _uuid(None),
                  "version": active.version + 1 if same and active else 1,
                  "race_id": self._race_id_in(connection, race_key),
                  "level": str(level) if level is not None else None,
                  "state": CycleVersionState.ACTIVE, "cause": {"kind": str(cause)},
                  "taken_at": now}
        found = self._insert_in(connection, trame, values)
        _audit(connection, actor, "cycle.transition", "cycle_version", found.id,
               _jsonable(values))
        return found

    def propose_cycle_in(self, connection: Connection, trame: Trame, decision: dict,
                         race_key: str, expires_at: dt.datetime, *,
                         actor: str = "application") -> CycleVersion:
        """A preparation proposed to the athlete, open until `expires_at`; it replaces the
        proposal still open, and changes nothing in force until it is taken."""
        self._lock_in(connection)
        now = dt.datetime.now(dt.UTC)
        connection.execute(cycle_versions.update().where(
            cycle_versions.c.athlete_id == self.athlete_id,
            cycle_versions.c.state == CycleVersionState.PROPOSED).values(
            state=CycleVersionState.SUPERSEDED, settled_at=now))
        race_id = self._race_id_in(connection, race_key)
        if race_id is None:
            raise StorageError(f"no race {race_key} to propose a preparation for")
        values = {"cycle_key": _uuid(None), "version": 1, "race_id": race_id,
                  "state": CycleVersionState.PROPOSED, "cause": {"kind": str(CycleCause.CREATE)},
                  "decision": decision, "expires_at": expires_at}
        found = self._insert_in(connection, trame, values)
        _audit(connection, actor, "cycle.proposed", "cycle_version", found.id,
               _jsonable(values))
        return found

    def open_cycle_proposal(self, now: dt.datetime) -> CycleVersion | None:
        """The proposal the athlete may still take."""
        with self.database.engine.connect() as connection:
            found = self._versions_in(connection,
                                      cycle_versions.c.state == CycleVersionState.PROPOSED,
                                      cycle_versions.c.expires_at > now)
        return found[-1] if found else None

    def take_cycle_in(self, connection: Connection, version_id: uuid.UUID, now: dt.datetime, *,
                      actor: str = "application") -> CycleVersion | None:
        """The athlete takes a proposal still open: it is in force at once, the active
        version closed in the same transaction. None when it is no longer open."""
        self._lock_in(connection)
        open_ = connection.execute(sa.select(cycle_versions.c.id).where(
            cycle_versions.c.athlete_id == self.athlete_id, cycle_versions.c.id == version_id,
            cycle_versions.c.state == CycleVersionState.PROPOSED,
            cycle_versions.c.expires_at > now)).scalar_one_or_none()
        if open_ is None:
            return None
        active = self.active_cycle_in(connection)
        if active is not None:
            connection.execute(cycle_versions.update().where(
                cycle_versions.c.id == active.id).values(
                state=CycleVersionState.CLOSED, settled_at=now))
        connection.execute(cycle_versions.update().where(
            cycle_versions.c.id == version_id).values(
            state=CycleVersionState.ACTIVE, taken_at=now))
        _audit(connection, actor, "cycle.taken", "cycle_version", version_id,
               {"closed": str(active.id) if active is not None else None})
        return self._versions_in(connection, cycle_versions.c.id == version_id)[0]

    @staticmethod
    def _enqueue_in(connection: Connection, key: str, kind: str, detail: dict,
                    due: dt.datetime, expires: dt.datetime | None = None) -> bool:
        return connection.execute(pg_insert(proactive_jobs).values(
            job_key=key, kind=kind, detail=json.dumps(detail, ensure_ascii=False, sort_keys=True),
            due_at=due, expires_at=expires,
        ).on_conflict_do_nothing().returning(proactive_jobs.c.job_key)).scalar_one_or_none() \
            is not None

    def announce_cycle_in(self, connection: Connection, version: CycleVersion,
                          now: dt.datetime) -> bool:
        """The message of a version put in force, rendered by the code."""
        return self._enqueue_in(connection, f"cycle-transition:{version.id}",
                                ProactiveKind.CYCLE_TRANSITION,
                                cycle_model.announcement(version), now)

    def offer_cycle_in(self, connection: Connection, version: CycleVersion, race: str,
                       expires_at: dt.datetime, now: dt.datetime) -> bool:
        """The message of a proposal, with the two answers the athlete taps."""
        return self._enqueue_in(connection, f"cycle-proposal:{version.id}",
                                ProactiveKind.CYCLE_PROPOSAL,
                                cycle_model.proposal(version, race, expires_at.date()), now,
                                expires_at)

    def expire_cycle_proposals_in(self, connection: Connection, now: dt.datetime) -> int:
        """The proposals left untaken past their day: expired, nothing else changes."""
        return connection.execute(cycle_versions.update().where(
            cycle_versions.c.athlete_id == self.athlete_id,
            cycle_versions.c.state == CycleVersionState.PROPOSED,
            cycle_versions.c.expires_at <= now).values(
            state=CycleVersionState.EXPIRED, settled_at=now)).rowcount


def _with(version_id: uuid.UUID, rows: Iterable[dict]) -> list[dict]:
    return [{**row, "cycle_version_id": version_id} for row in rows]
