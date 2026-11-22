"""Keeps the service's verdict on a run's execution against its typed prescription.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("strava_activity_intakes", sa.Column("debrief_verdict", sa.Text),
                  schema="redline")
    op.add_column("strava_activity_intakes",
                  sa.Column("debrief_findings", JSONB(none_as_null=True)), schema="redline")
    op.create_check_constraint(
        "ck_strava_activity_intakes_debrief_verdict", "strava_activity_intakes",
        "debrief_verdict IS NULL OR debrief_verdict IN "
        "('conforming','execution_gap','undetermined')",
        schema="redline")
    op.create_check_constraint(
        "ck_strava_activity_intakes_debrief_findings", "strava_activity_intakes",
        "(debrief_verdict IS NULL) = (debrief_findings IS NULL) AND (debrief_findings IS "
        "NULL OR jsonb_typeof(debrief_findings) = 'array')",
        schema="redline")


def downgrade() -> None:
    for name in ("ck_strava_activity_intakes_debrief_findings",
                 "ck_strava_activity_intakes_debrief_verdict"):
        op.drop_constraint(name, "strava_activity_intakes", schema="redline")
    op.drop_column("strava_activity_intakes", "debrief_findings", schema="redline")
    op.drop_column("strava_activity_intakes", "debrief_verdict", schema="redline")
