"""Keeps the revisions the coach proposes for a week until the athlete takes one.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

KINDS_BEFORE = (
    "observation.add", "activity.link", "measurement.complete", "measurement.hr_doubt",
    "exposure.upsert", "plan.revise", "daily.upsert", "capability.evaluate", "thread.upsert",
    "question.review", "question.ask", "deadline.upsert", "session.start", "session.match",
    "proactive.angle", "gear.upsert", "prediction.upsert", "correction.register",
    "permit.seal")
KINDS_AFTER = (*KINDS_BEFORE, "plan.propose")


def _kinds(kinds: tuple[str, ...]) -> str:
    return ("kind IN (" + ",".join(f"'{kind}'" for kind in kinds) + ") OR "
            "(kind = 'proactive_message.add' AND applied_at IS NOT NULL)")


def upgrade() -> None:
    op.create_table(
        "plan_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
        sa.Column("week", sa.Text, nullable=False),
        sa.Column("based_on", sa.SmallInteger, nullable=False),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("revision", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.Text),
        sa.CheckConstraint("char_length(title) BETWEEN 3 AND 80", name="ck_plan_proposals_title"),
        sa.CheckConstraint("position BETWEEN 1 AND 3", name="ck_plan_proposals_position"),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('taken','dismissed','superseded')",
                           name="ck_plan_proposals_outcome"),
        sa.CheckConstraint("(outcome IS NULL) = (settled_at IS NULL)",
                           name="ck_plan_proposals_settled"),
        schema="redline")
    op.drop_constraint("ck_turn_commands_kind", "turn_commands", schema="redline")
    op.create_check_constraint("ck_turn_commands_kind", "turn_commands", _kinds(KINDS_AFTER),
                               schema="redline")


def downgrade() -> None:
    op.drop_constraint("ck_turn_commands_kind", "turn_commands", schema="redline")
    op.create_check_constraint("ck_turn_commands_kind", "turn_commands", _kinds(KINDS_BEFORE),
                               schema="redline")
    op.drop_table("plan_proposals", schema="redline")
