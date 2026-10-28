"""A typed prediction says why it failed or could not be tested.

`coach_predictions.outcome_reason`: `optimistic` or `cautious` for a refuted one,
`instrument`, `exposure`, `scope` or `superseded` for a void one. The narrative
predictions of `decisions.md` carried these qualifiers in prose; predictions now live
in this table only.

Revision ID: 20261028_0006
Revises: 20261028_0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261028_0006"
down_revision = "20261028_0005"
branch_labels = None
depends_on = None

NAME = "ck_coach_predictions_outcome_reason"
RULE = ("outcome_reason IS NULL OR "
        "(status = 'resolved' AND outcome = false AND outcome_reason IN "
        "('optimistic', 'cautious')) OR "
        "(status = 'void' AND outcome_reason IN "
        "('instrument', 'exposure', 'scope', 'superseded'))")


def upgrade() -> None:
    op.add_column("coach_predictions", sa.Column("outcome_reason", sa.Text),
                  schema="redline")
    op.create_check_constraint(NAME, "coach_predictions", RULE, schema="redline")


def downgrade() -> None:
    op.drop_constraint(NAME, "coach_predictions", type_="check", schema="redline")
    op.drop_column("coach_predictions", "outcome_reason", schema="redline")
