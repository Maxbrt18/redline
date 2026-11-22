"""Types the dose of a session of repetitions: count, duration, recovery, pace.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

COLUMNS = (
    ("reps", sa.SmallInteger), ("rep_seconds", sa.Integer), ("recovery_seconds", sa.Integer),
    ("rep_pace_s", sa.SmallInteger),
)
CHECKS = (
    ("ck_planned_sessions_reps",
     "(reps IS NULL) = (rep_seconds IS NULL) AND (reps IS NULL OR reps BETWEEN 1 AND 60) "
     "AND (rep_seconds IS NULL OR rep_seconds BETWEEN 10 AND 3600)"),
    ("ck_planned_sessions_rep_dose",
     "(recovery_seconds IS NULL OR (reps IS NOT NULL AND recovery_seconds BETWEEN 0 AND "
     "1800)) AND (rep_pace_s IS NULL OR (reps IS NOT NULL AND rep_pace_s BETWEEN 120 AND "
     "900))"),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("planned_sessions", sa.Column(name, kind), schema="redline")
    for name, condition in CHECKS:
        op.create_check_constraint(name, "planned_sessions", condition, schema="redline")


def downgrade() -> None:
    for name, _ in reversed(CHECKS):
        op.drop_constraint(name, "planned_sessions", schema="redline")
    for name, _ in reversed(COLUMNS):
        op.drop_column("planned_sessions", name, schema="redline")
