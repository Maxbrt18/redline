"""Says what a planned session weighs when the athlete's days run short.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("planned_sessions", sa.Column("priority", sa.Text), schema="redline")
    op.create_check_constraint(
        "ck_planned_sessions_priority", "planned_sessions",
        "priority IS NULL OR priority IN ('key','support','optional')",
        schema="redline")


def downgrade() -> None:
    op.drop_constraint("ck_planned_sessions_priority", "planned_sessions", schema="redline")
    op.drop_column("planned_sessions", "priority", schema="redline")
