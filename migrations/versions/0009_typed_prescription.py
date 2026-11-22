"""Types the prescription of a planned run: relief, climb and descent intents, structure.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

COLUMNS = (
    ("dplus_cap_m", sa.Integer), ("dplus_target_m", sa.Integer), ("dminus_cap_m", sa.Integer),
    ("climb_intent", sa.Text), ("power_hike_from_pct", sa.SmallInteger),
    ("descent_intent", sa.Text), ("structure", sa.Text),
)
CHECKS = (
    ("ck_planned_sessions_relief",
     "(dplus_cap_m IS NULL OR dplus_cap_m >= 0) AND (dplus_target_m IS NULL OR "
     "dplus_target_m >= 0) AND (dminus_cap_m IS NULL OR dminus_cap_m >= 0)"),
    ("ck_planned_sessions_dplus_order",
     "dplus_target_m IS NULL OR dplus_cap_m IS NULL OR dplus_target_m <= dplus_cap_m"),
    ("ck_planned_sessions_climb_intent",
     "climb_intent IS NULL OR climb_intent IN "
     "('easy','run_slow','race_effort','hard','power_hike')"),
    ("ck_planned_sessions_power_hike",
     "(climb_intent IS NOT DISTINCT FROM 'power_hike') = (power_hike_from_pct IS NOT NULL) "
     "AND (power_hike_from_pct IS NULL OR power_hike_from_pct BETWEEN 3 AND 40)"),
    ("ck_planned_sessions_descent_intent",
     "descent_intent IS NULL OR descent_intent IN ('relaxed','held','worked')"),
    ("ck_planned_sessions_structure",
     "structure IS NULL OR structure IN ('steady','efforts')"),
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
