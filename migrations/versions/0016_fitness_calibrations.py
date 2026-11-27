"""Keeps the calibrations of the athlete's fitness, append-only.

The table starts empty: the service's daily pass writes the first calibration from the
declared all-out efforts.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fitness_calibrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("athlete_id", sa.Text, sa.ForeignKey("redline.athletes.id"), nullable=False),
        sa.Column("adopted_on", sa.Date, nullable=False),
        sa.Column("cs_mps", sa.Numeric(5, 3)),
        sa.Column("d_prime_m", sa.Numeric(6, 1)),
        sa.Column("cs_se_mps", sa.Numeric(5, 3)),
        sa.Column("vdot", sa.Numeric(5, 2)),
        sa.Column("vdot_sd", sa.Numeric(5, 2)),
        sa.Column("hr_first_threshold_bpm", sa.SmallInteger),
        sa.Column("hr_second_threshold_bpm", sa.SmallInteger),
        sa.Column("confidence", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("decided_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("timezone('utc', now())")),
        sa.CheckConstraint("cs_mps IS NOT NULL OR vdot IS NOT NULL",
                           name="ck_fitness_calibrations_reference"),
        sa.CheckConstraint("(cs_mps IS NULL) = (d_prime_m IS NULL)",
                           name="ck_fitness_calibrations_critical_speed"),
        sa.CheckConstraint("(vdot IS NULL) = (vdot_sd IS NULL)",
                           name="ck_fitness_calibrations_vdot"),
        sa.CheckConstraint("confidence IN ('provisional','measured')",
                           name="ck_fitness_calibrations_confidence"),
        sa.CheckConstraint("source IN ('test','race','initial')",
                           name="ck_fitness_calibrations_source"),
        sa.CheckConstraint("decided_by IN ('rule','request')",
                           name="ck_fitness_calibrations_decided_by"),
        schema="redline")
    op.execute("""
        CREATE FUNCTION redline.forbid_fitness_calibration_mutation() RETURNS trigger
            LANGUAGE plpgsql
            AS $$ BEGIN
                  RAISE EXCEPTION 'a fitness calibration is append-only';
                END; $$""")
    op.execute("""
        CREATE TRIGGER trg_fitness_calibration_immutable
            BEFORE DELETE OR UPDATE ON redline.fitness_calibrations
            FOR EACH ROW EXECUTE FUNCTION redline.forbid_fitness_calibration_mutation()""")


def downgrade() -> None:
    op.drop_table("fitness_calibrations", schema="redline")
    op.execute("DROP FUNCTION redline.forbid_fitness_calibration_mutation()")
