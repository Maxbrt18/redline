"""Transition kinds come from the instance's rulebook, no longer from the database.

`ck_planned_sessions_transition` enumerated the six kinds the engine knew. They now
belong to `rulebook.toml` and are checked when the plan is written: the constraint only
keeps the shape of a slug.

The downgrade restores the original list; it fails if a session carries a kind the list
does not contain, rather than erasing that session's kind.

Revision ID: 20261027_0003
Revises: 20261026_0002
"""

from __future__ import annotations

from alembic import op

revision = "20261027_0003"
down_revision = "20261026_0002"
branch_labels = None
depends_on = None

NAME = "ck_planned_sessions_transition"
SLUG = "transition IS NULL OR transition ~ '^[a-z0-9][a-z0-9-]{0,39}$'"
LISTED = ("transition IS NULL OR transition IN "
          "('qualite','descente','nocturne','terrain','reconnaissance','injury')")


def upgrade() -> None:
    op.drop_constraint(NAME, "planned_sessions", type_="check", schema="redline")
    op.create_check_constraint(NAME, "planned_sessions", SLUG, schema="redline")


def downgrade() -> None:
    op.drop_constraint(NAME, "planned_sessions", type_="check", schema="redline")
    op.create_check_constraint(NAME, "planned_sessions", LISTED, schema="redline")
