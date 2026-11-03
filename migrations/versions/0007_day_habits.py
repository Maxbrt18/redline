"""Keeps how the athlete's days present themselves: weekly habits and days stated ahead.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

KINDS = "('as_planned','off','short','easy','more','with_others','other_sport')"
PACES = "('easy','mixed','workout')"
UTC_NOW = sa.text("timezone('utc', now())")


def _day_state(table: str) -> list:
    return [
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("minutes", sa.SmallInteger),
        sa.Column("pace", sa.Text),
        sa.Column("required", sa.Boolean),
        sa.Column("sport", sa.Text),
        sa.Column("demanding", sa.Boolean),
        sa.Column("run_too", sa.Boolean),
        sa.CheckConstraint(f"kind IN {KINDS}", name=f"ck_{table}_kind"),
        sa.CheckConstraint("minutes IS NULL OR minutes BETWEEN 10 AND 600",
                           name=f"ck_{table}_minutes"),
        sa.CheckConstraint(f"pace IS NULL OR pace IN {PACES}", name=f"ck_{table}_pace"),
        sa.CheckConstraint("sport IS NULL OR char_length(sport) BETWEEN 1 AND 40",
                           name=f"ck_{table}_sport"),
    ]


def upgrade() -> None:
    op.create_table(
        "day_habits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
        sa.Column("weekday", sa.SmallInteger, nullable=False),
        sa.Column("starts_on", sa.Date, nullable=False),
        sa.Column("ends_on", sa.Date),
        *_day_state("day_habits"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=UTC_NOW),
        sa.CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_day_habits_weekday"),
        sa.CheckConstraint("ends_on IS NULL OR starts_on <= ends_on",
                           name="ck_day_habits_range"),
        schema="redline")
    op.create_table(
        "stated_days",
        sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"),
                  primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        *_day_state("stated_days"),
        sa.Column("stated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=UTC_NOW),
        schema="redline")


def downgrade() -> None:
    op.drop_table("stated_days", schema="redline")
    op.drop_table("day_habits", schema="redline")
