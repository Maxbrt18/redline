"""The versions of the training cycles: the active one, and the transitions that replace it.

A version is written whole, trame included, in one transaction, and the one it replaces
is settled in the same transaction: there is never a moment with two cycles in force.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable

import sqlalchemy as sa
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
)

from ..schema import cycle_key_sessions, cycle_versions, cycle_weeks, deadlines
from ._common import StoreBase, _audit, _jsonable, _native, _uuid

#: The kinds of cycle whose weeks are planned low: they are not a start.
LOW_KINDS = (CycleKind.RECOVERY, CycleKind.REBUILD)


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

    def apply_transition_in(self, connection: Connection, trame: Trame, cause: CycleCause, *,
                            level: GeneralLevel | None = None, race_key: str | None = None,
                            actor: str = "application") -> CycleVersion:
        """The next cycle, in force at once: the next block of a general preparation is a
        new version of its cycle, any other transition a new cycle. The active version is
        superseded or closed in the same transaction."""
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('redline:cycle'))"))
        now = dt.datetime.now(dt.UTC)
        active = self.active_cycle_in(connection)
        same = active is not None and cause == CycleCause.BLOCK_END
        if active is not None:
            connection.execute(cycle_versions.update().where(
                cycle_versions.c.id == active.id).values(
                state=CycleVersionState.SUPERSEDED if same else CycleVersionState.CLOSED,
                settled_at=now))
        version_id = _uuid(None)
        values = {"id": version_id, "athlete_id": self.athlete_id,
                  "cycle_key": active.cycle_key if same and active else _uuid(None),
                  "version": active.version + 1 if same and active else 1,
                  "race_id": self._race_id_in(connection, race_key),
                  "level": str(level) if level is not None else None,
                  "state": CycleVersionState.ACTIVE, "cause": {"kind": str(cause)},
                  "taken_at": now, **cycle_model.version_values(trame)}
        connection.execute(cycle_versions.insert().values(**values))
        connection.execute(cycle_weeks.insert(), _with(version_id,
                                                       cycle_model.week_values(trame)))
        keys = cycle_model.key_values(trame)
        if keys:
            connection.execute(cycle_key_sessions.insert(),
                               [{**row, "id": _uuid(None)} for row in _with(version_id, keys)])
        _audit(connection, actor, "cycle.transition", "cycle_version", version_id,
               _jsonable({key: value for key, value in values.items()
                          if key not in ("hypotheses", "risks")}))
        found = self._versions_in(connection, cycle_versions.c.id == version_id)
        return found[0]


def _with(version_id: uuid.UUID, rows: Iterable[dict]) -> list[dict]:
    return [{**row, "cycle_version_id": version_id} for row in rows]
