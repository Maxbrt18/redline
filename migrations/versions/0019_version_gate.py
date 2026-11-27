"""Links a planned session to the key session it runs and keeps the overrides of a
revision; retires the taper's keys from the races' contexts.

A race tapers as its cycle template says: `taper_weeks`, `taper_cut_min_pct` and
`taper_cut_max_pct` are removed from the contexts, and a downgrade does not bring them
back. The format-1 plan templates and their `base` phase are gone.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TAPER_KEYS = ("taper_weeks", "taper_cut_min_pct", "taper_cut_max_pct")
PHASES_BEFORE = ("recovery", "base", "development", "specific", "taper", "general", "rebuild")
PHASES_AFTER = tuple(phase for phase in PHASES_BEFORE if phase != "base")


def _listed(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _phases(phases: tuple[str, ...]) -> None:
    op.drop_constraint("ck_cycle_weeks_phase", "cycle_weeks", schema="redline")
    op.create_check_constraint("ck_cycle_weeks_phase", "cycle_weeks",
                               f"phase IN ({_listed(phases)})", schema="redline")


def upgrade() -> None:
    op.add_column("planned_sessions",
                  sa.Column("key_session_id", UUID(as_uuid=True),
                            sa.ForeignKey("redline.cycle_key_sessions.id")),
                  schema="redline")
    op.add_column("plan_revisions", sa.Column("cycle_overrides", JSONB), schema="redline")
    _phases(PHASES_AFTER)
    removed = " - ".join(f"'{key}'" for key in TAPER_KEYS)
    op.execute(f"""
        UPDATE redline.deadlines SET context = context - {removed}
         WHERE kind = 'race' AND context ?| array[{_listed(TAPER_KEYS)}]""")


def downgrade() -> None:
    _phases(PHASES_BEFORE)
    op.drop_column("plan_revisions", "cycle_overrides", schema="redline")
    op.drop_column("planned_sessions", "key_session_id", schema="redline")
