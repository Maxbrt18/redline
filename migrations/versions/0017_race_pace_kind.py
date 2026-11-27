"""Lets a session be run, and planned, at race pace.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

RECORDED_BEFORE = ("easy", "long", "threshold", "hills", "strides", "cadence", "race", "hike",
                   "bike", "strength", "wake-trace", "rest-reading", "instruments", "palpation")
PLANNED_BEFORE = ("easy", "long", "threshold", "hills", "strides", "cadence", "race", "hike",
                  "bike", "strength", "test", "rest")


def _with_race_pace(kinds: tuple[str, ...]) -> tuple[str, ...]:
    index = kinds.index("hills") + 1
    return (*kinds[:index], "race-pace", *kinds[index:])


def _listed(kinds: tuple[str, ...]) -> str:
    return ",".join(f"'{kind}'" for kind in kinds)


def _replace(recorded: tuple[str, ...], planned: tuple[str, ...]) -> None:
    op.drop_constraint("ck_observations_kind", "observations", schema="redline")
    op.create_check_constraint("ck_observations_kind", "observations",
                               f"kind IN ({_listed(recorded)})", schema="redline")
    op.drop_constraint("ck_planned_sessions_workout", "planned_sessions", schema="redline")
    op.create_check_constraint("ck_planned_sessions_workout", "planned_sessions",
                               f"workout IN ({_listed(planned)})", schema="redline")


def upgrade() -> None:
    _replace(_with_race_pace(RECORDED_BEFORE), _with_race_pace(PLANNED_BEFORE))


def downgrade() -> None:
    _replace(RECORDED_BEFORE, PLANNED_BEFORE)
