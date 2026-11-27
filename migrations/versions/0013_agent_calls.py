"""Registers what each model provider call consumed: tokens, duration and API-price cost.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# The service writes a call once and never rewrites it. The grant is explicit: default
# privileges only apply when the migration runs as `redline_owner`.
PRIVILEGES = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'redline_app') THEN
    GRANT SELECT, INSERT ON redline.agent_calls TO redline_app;
    REVOKE UPDATE, DELETE ON redline.agent_calls FROM redline_app;
  END IF;
END $$
"""


def upgrade() -> None:
    op.create_table(
        "agent_calls",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", UUID(as_uuid=True)),
        sa.Column("psid", sa.Text),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("purpose", sa.Text, nullable=False),
        sa.Column("model", sa.Text),
        sa.Column("cost_usd", sa.Numeric(12, 6)),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.PrimaryKeyConstraint("id", name="pk_agent_calls"),
        sa.CheckConstraint("provider IN ('codex','claude')", name="ck_agent_calls_provider"),
        sa.CheckConstraint("purpose IN ('turn','triage')", name="ck_agent_calls_purpose"),
        sa.CheckConstraint("psid IS NULL OR psid <> ''", name="ck_agent_calls_psid"),
        sa.CheckConstraint("purpose <> 'turn' OR turn_id IS NOT NULL",
                           name="ck_agent_calls_turn"),
        sa.CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="ck_agent_calls_cost"),
        sa.CheckConstraint("input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 "
                           "AND cache_write_tokens >= 0", name="ck_agent_calls_tokens"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0",
                           name="ck_agent_calls_duration"),
        schema="redline",
    )
    op.create_index("ix_agent_calls_created_at", "agent_calls", ["created_at"],
                    schema="redline")
    op.create_index("ix_agent_calls_turn_id", "agent_calls", ["turn_id"], schema="redline")
    op.execute(PRIVILEGES)


def downgrade() -> None:
    op.drop_index("ix_agent_calls_turn_id", "agent_calls", schema="redline")
    op.drop_index("ix_agent_calls_created_at", "agent_calls", schema="redline")
    op.drop_table("agent_calls", schema="redline")
