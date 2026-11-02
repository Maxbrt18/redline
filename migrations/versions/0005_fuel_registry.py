"""Adds the fuel registry: formats, sugars taken, kind and onset of digestive trouble.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLE = "observation_metrics"
STATES = "('pending','known','unavailable','not_applicable')"
MEASUREMENTS = {
    "fuel_formats": (sa.ARRAY(sa.Text), "cardinality(value) > 0 AND "
                     "value <@ ARRAY['drink','gel','chew','solid']::text[]"),
    "fuel_mix": (sa.Text, "value IN ('glucose','glucose_fructose')"),
    "gut_symptom_kinds": (sa.ARRAY(sa.Text), "cardinality(value) > 0 AND value <@ "
                          "ARRAY['nausea','reflux','cramps','bloating','urgency']::text[]"),
    "gut_onset_min": (sa.SmallInteger, "value BETWEEN 0 AND 1440"),
}


def upgrade() -> None:
    for metric, (column_type, known_range) in MEASUREMENTS.items():
        state = f"{metric}_state"
        op.add_column(TABLE, sa.Column(metric, column_type), schema="redline")
        # Existing rows never asked for these figures: they are not applicable. The server
        # default only fills them; like every other state column, it has none afterwards.
        op.add_column(TABLE, sa.Column(state, sa.Text, nullable=False,
                                       server_default="not_applicable"), schema="redline")
        op.alter_column(TABLE, state, server_default=None, schema="redline")
        op.create_check_constraint(f"ck_observation_metrics_{metric}_state", TABLE,
                                   f"{state} IN {STATES}", schema="redline")
        op.create_check_constraint(
            f"ck_observation_metrics_{metric}_value", TABLE,
            f"({state} = 'known' AND {metric} IS NOT NULL AND "
            f"({known_range.replace('value', metric)})) OR "
            f"({state} <> 'known' AND {metric} IS NULL)", schema="redline")


def downgrade() -> None:
    for metric in reversed(MEASUREMENTS):
        op.drop_constraint(f"ck_observation_metrics_{metric}_value", TABLE, schema="redline")
        op.drop_constraint(f"ck_observation_metrics_{metric}_state", TABLE, schema="redline")
        op.drop_column(TABLE, f"{metric}_state", schema="redline")
        op.drop_column(TABLE, metric, schema="redline")
