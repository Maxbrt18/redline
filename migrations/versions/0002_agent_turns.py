"""Registers what produced each model turn, and links a coach message to its turn.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# The service writes a turn once and never rewrites it. The grant is explicit: default
# privileges only apply when the migration runs as `redline_owner`.
PRIVILEGES = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'redline_app') THEN
    GRANT SELECT, INSERT ON redline.agent_turns TO redline_app;
    REVOKE UPDATE, DELETE ON redline.agent_turns FROM redline_app;
  END IF;
END $$
"""


def upgrade() -> None:
    op.create_table(
        "agent_turns",
        sa.Column("turn_id", UUID(as_uuid=True), nullable=False),
        sa.Column("psid", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("route", sa.Text, nullable=False),
        sa.Column("model", sa.Text),
        sa.Column("model_reported", sa.Boolean, nullable=False),
        sa.Column("effort", sa.Text),
        sa.Column("instruction_digest", sa.Text, nullable=False),
        sa.Column("rulebook_digest", sa.Text, nullable=False),
        sa.Column("engine_tree", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.PrimaryKeyConstraint("turn_id", name="pk_agent_turns"),
        sa.CheckConstraint("provider IN ('codex','claude')", name="ck_agent_turns_provider"),
        sa.CheckConstraint("psid <> '' AND route <> '' AND instruction_digest <> '' "
                           "AND rulebook_digest <> ''", name="ck_agent_turns_identity"),
        sa.CheckConstraint("NOT model_reported OR model IS NOT NULL",
                           name="ck_agent_turns_model_reported"),
        schema="redline",
    )
    op.add_column("conversation_messages", sa.Column("turn_id", UUID(as_uuid=True)),
                  schema="redline")
    op.create_foreign_key(
        "fk_conversation_messages_turn_id_agent_turns", "conversation_messages",
        "agent_turns", ["turn_id"], ["turn_id"],
        source_schema="redline", referent_schema="redline")
    op.create_check_constraint(
        "ck_conversation_messages_turn_id", "conversation_messages",
        "role <> 'athlete' OR turn_id IS NULL", schema="redline")
    op.execute(PRIVILEGES)


def downgrade() -> None:
    op.drop_constraint("ck_conversation_messages_turn_id", "conversation_messages",
                       schema="redline")
    op.drop_constraint("fk_conversation_messages_turn_id_agent_turns",
                       "conversation_messages", schema="redline")
    op.drop_column("conversation_messages", "turn_id", schema="redline")
    op.drop_table("agent_turns", schema="redline")
