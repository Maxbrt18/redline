"""Lets a trame carry the first times its race requires, as key sessions of their own.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

ROLES_BEFORE = ("key", "flagship", "test")
ROLES_AFTER = (*ROLES_BEFORE, "first")


def _roles(roles: tuple[str, ...]) -> None:
    listed = ",".join(f"'{role}'" for role in roles)
    op.drop_constraint("ck_cycle_key_sessions_role", "cycle_key_sessions", schema="redline")
    op.create_check_constraint("ck_cycle_key_sessions_role", "cycle_key_sessions",
                               f"role IN ({listed})", schema="redline")


def upgrade() -> None:
    op.add_column("cycle_key_sessions", sa.Column("transition", sa.Text), schema="redline")
    _roles(ROLES_AFTER)
    op.create_check_constraint("ck_cycle_key_sessions_first", "cycle_key_sessions",
                               "(role = 'first') = (transition IS NOT NULL)", schema="redline")


def downgrade() -> None:
    op.execute("DELETE FROM redline.cycle_key_sessions WHERE role = 'first'")
    op.drop_constraint("ck_cycle_key_sessions_first", "cycle_key_sessions", schema="redline")
    _roles(ROLES_BEFORE)
    op.drop_column("cycle_key_sessions", "transition", schema="redline")
