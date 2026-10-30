"""Names the sugars a planned session's fuelling target is taken as.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("planned_sessions", sa.Column("carbs_mix", sa.Text), schema="redline")
    op.create_check_constraint(
        "ck_planned_sessions_carbs_mix", "planned_sessions",
        "carbs_mix IS NULL OR (carbs_target_g_h IS NOT NULL "
        "AND carbs_mix IN ('glucose','glucose_fructose'))",
        schema="redline")


def downgrade() -> None:
    op.drop_constraint("ck_planned_sessions_carbs_mix", "planned_sessions", schema="redline")
    op.drop_column("planned_sessions", "carbs_mix", schema="redline")
