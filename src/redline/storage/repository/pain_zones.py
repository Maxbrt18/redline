"""The monitored pain zones: the reference every pain reading is checked against."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from ..schema import pain_zones
from ._common import StoreBase, _audit


class PainZones(StoreBase):
    def sync_pain_zones(self, zones: list[tuple[str, str]], *,
                        actor: str = "application") -> None:
        """Align the reference on `[[injuries]]`: a removed zone becomes inactive.

        Never deleted: its past readings stay attached and readable.
        """
        with self.database.transaction() as connection:
            declared = {slug for slug, _ in zones}
            for slug, label in zones:
                connection.execute(pg_insert(pain_zones).values(
                    athlete_id=self.athlete_id, slug=slug, label=label, active=True,
                ).on_conflict_do_update(
                    index_elements=[pain_zones.c.athlete_id, pain_zones.c.slug],
                    set_={"label": label, "active": True}))
            connection.execute(pain_zones.update().where(
                pain_zones.c.athlete_id == self.athlete_id,
                pain_zones.c.slug.not_in(declared) if declared else sa.true(),
            ).values(active=False))
            _audit(connection, actor, "pain_zones.synced", "athlete",
                   uuid.uuid5(uuid.NAMESPACE_URL, f"athlete:{self.athlete_id}"),
                   {"zones": sorted(declared)})

    def pain_zones(self) -> list[dict]:
        """Monitored zones, active first, in declaration order."""
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(
                pain_zones.c.slug, pain_zones.c.label, pain_zones.c.active,
            ).where(pain_zones.c.athlete_id == self.athlete_id).order_by(
                pain_zones.c.active.desc(), pain_zones.c.created_at,
                pain_zones.c.slug)).mappings()]

    def _active_zones_in(self, connection: Connection) -> list[str]:
        return list(connection.execute(sa.select(pain_zones.c.slug).where(
            pain_zones.c.athlete_id == self.athlete_id, pain_zones.c.active,
        ).order_by(pain_zones.c.created_at, pain_zones.c.slug)).scalars())
