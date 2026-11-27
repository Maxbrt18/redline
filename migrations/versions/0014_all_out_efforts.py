"""Keeps the efforts the athlete ran all out and the breaks declared since.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

KINDS_BEFORE = (
    "observation.add", "activity.link", "measurement.complete", "measurement.hr_doubt",
    "exposure.upsert", "plan.revise", "daily.upsert", "capability.evaluate", "thread.upsert",
    "question.review", "question.ask", "deadline.upsert", "session.start", "session.match",
    "proactive.angle", "gear.upsert", "prediction.upsert", "correction.register",
    "permit.seal", "plan.propose")
KINDS_AFTER = (*KINDS_BEFORE, "effort.upsert", "break.upsert")
UTC_NOW = sa.text("timezone('utc', now())")


def _kinds(kinds: tuple[str, ...]) -> str:
    return ("kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ") OR "
            "(kind = 'proactive_message.add' AND applied_at IS NOT NULL)")


def upgrade() -> None:
    op.create_table(
        "all_out_efforts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
        sa.Column("effort_key", sa.Text, nullable=False),
        sa.Column("done_on", sa.Date, nullable=False),
        sa.Column("distance_km", sa.Numeric(7, 3), nullable=False),
        sa.Column("duration_s", sa.Numeric(8, 1), nullable=False),
        sa.Column("terrain", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=UTC_NOW),
        sa.CheckConstraint("effort_key <> ''", name="ck_all_out_efforts_key"),
        sa.CheckConstraint("distance_km > 0", name="ck_all_out_efforts_distance"),
        sa.CheckConstraint("duration_s > 0", name="ck_all_out_efforts_duration"),
        sa.CheckConstraint("terrain IN ('flat','hilly')", name="ck_all_out_efforts_terrain"),
        sa.CheckConstraint("kind IN ('race','test')", name="ck_all_out_efforts_kind"),
        sa.UniqueConstraint("athlete_id", "effort_key", name="uq_all_out_efforts_key"),
        schema="redline")
    op.create_table(
        "training_breaks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
        sa.Column("break_key", sa.Text, nullable=False),
        sa.Column("starts_on", sa.Date, nullable=False),
        sa.Column("ends_on", sa.Date, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=UTC_NOW),
        sa.CheckConstraint("break_key <> ''", name="ck_training_breaks_key"),
        sa.CheckConstraint("starts_on <= ends_on", name="ck_training_breaks_range"),
        sa.UniqueConstraint("athlete_id", "break_key", name="uq_training_breaks_key"),
        schema="redline")
    op.drop_constraint("ck_turn_commands_kind", "turn_commands", schema="redline")
    op.create_check_constraint("ck_turn_commands_kind", "turn_commands", _kinds(KINDS_AFTER),
                               schema="redline")


def downgrade() -> None:
    op.drop_constraint("ck_turn_commands_kind", "turn_commands", schema="redline")
    op.create_check_constraint("ck_turn_commands_kind", "turn_commands", _kinds(KINDS_BEFORE),
                               schema="redline")
    op.drop_table("training_breaks", schema="redline")
    op.drop_table("all_out_efforts", schema="redline")
