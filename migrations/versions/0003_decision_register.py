"""Keeps what settled each plan revision, the options it set aside and the visible state.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_revisions", sa.Column("decision", JSONB), schema="redline")
    op.add_column("plan_revisions", sa.Column("state_snapshot", JSONB), schema="redline")
    op.create_check_constraint(
        "ck_plan_revisions_decision", "plan_revisions",
        "decision IS NULL OR decision->>'by' IN ('rule','judgment','request')",
        schema="redline")


def downgrade() -> None:
    op.drop_constraint("ck_plan_revisions_decision", "plan_revisions", schema="redline")
    op.drop_column("plan_revisions", "state_snapshot", schema="redline")
    op.drop_column("plan_revisions", "decision", schema="redline")
