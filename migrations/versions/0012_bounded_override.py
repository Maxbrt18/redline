"""Keeps why a planned session goes one step above its lineage's ceiling, and the
prediction sealed on that step.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("planned_sessions", sa.Column("override_reason", sa.Text), schema="redline")
    op.add_column("planned_sessions", sa.Column("override_prediction", sa.Text),
                  schema="redline")
    op.create_check_constraint(
        "ck_planned_sessions_override", "planned_sessions",
        "(override_reason IS NULL) = (override_prediction IS NULL) AND (override_reason IS "
        "NULL OR (reps IS NOT NULL AND override_reason IN "
        "('tolerated_more','race_demand','athlete_choice')))",
        schema="redline")


def downgrade() -> None:
    op.drop_constraint("ck_planned_sessions_override", "planned_sessions", schema="redline")
    op.drop_column("planned_sessions", "override_prediction", schema="redline")
    op.drop_column("planned_sessions", "override_reason", schema="redline")
