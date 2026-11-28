"""Keeps the engine's draft a week's first revision was written against.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_revisions", sa.Column("draft", JSONB), schema="redline")


def downgrade() -> None:
    op.drop_column("plan_revisions", "draft", schema="redline")
