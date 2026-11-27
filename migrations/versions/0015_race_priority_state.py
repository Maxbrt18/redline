"""Gives each race a priority and a state, and makes its status follow the state.

The races already written are taken up: a race done was run, a race still ahead is active,
the nearest one ahead the goal (priority A), the others B. A race run keeps priority A, so
its taper stays readable. The result is read by hand before production.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

PRIORITIES = "'a','b','c'"
STATES = "'proposed','active','postponed','cancelled','run','abandoned','dropped'"
CLOSED = "'cancelled','run','abandoned','dropped'"


def upgrade() -> None:
    op.add_column("deadlines", sa.Column("priority", sa.Text), schema="redline")
    op.add_column("deadlines", sa.Column("race_state", sa.Text), schema="redline")
    op.execute("""
        UPDATE redline.deadlines
           SET race_state = CASE WHEN status = 'done' THEN 'run' ELSE 'active' END,
               priority = CASE WHEN status = 'done' THEN 'a' ELSE 'b' END
         WHERE kind = 'race'""")
    op.execute("""
        UPDATE redline.deadlines SET priority = 'a'
         WHERE id IN (SELECT DISTINCT ON (athlete_id) id FROM redline.deadlines
                       WHERE kind = 'race' AND status <> 'done' AND due_on >= CURRENT_DATE
                       ORDER BY athlete_id, due_on, deadline_key)""")
    op.create_check_constraint("ck_deadlines_priority", "deadlines",
                               f"priority IS NULL OR priority IN ({PRIORITIES})",
                               schema="redline")
    op.create_check_constraint("ck_deadlines_race_state", "deadlines",
                               f"race_state IS NULL OR race_state IN ({STATES})",
                               schema="redline")
    op.create_check_constraint(
        "ck_deadlines_race_fields", "deadlines",
        "(kind = 'race' AND priority IS NOT NULL AND race_state IS NOT NULL) OR "
        "(kind <> 'race' AND priority IS NULL AND race_state IS NULL)", schema="redline")
    op.create_check_constraint(
        "ck_deadlines_race_status", "deadlines",
        f"kind <> 'race' OR ((status = 'done') = (race_state IN ({CLOSED})))",
        schema="redline")
    op.create_index("ix_deadlines_active_goal", "deadlines", ["athlete_id"], unique=True,
                    postgresql_where=sa.text("priority = 'a' AND race_state = 'active'"),
                    schema="redline")


def downgrade() -> None:
    op.drop_index("ix_deadlines_active_goal", "deadlines", schema="redline")
    for name in ("ck_deadlines_race_status", "ck_deadlines_race_fields",
                 "ck_deadlines_race_state", "ck_deadlines_priority"):
        op.drop_constraint(name, "deadlines", schema="redline")
    op.drop_column("deadlines", "race_state", schema="redline")
    op.drop_column("deadlines", "priority", schema="redline")
