"""A Strava capture whose activity could not be read has the `unknown` sport.

The engine writes `unknown` since the activity became typed; captures that failed before
kept the French `inconnu`. They are corrected; the downgrade puts the old value back.

Revision ID: 20261030_0015
Revises: 20261029_0014
"""

from __future__ import annotations

from alembic import op

revision = "20261030_0015"
down_revision = "20261029_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE redline.strava_activity_intakes SET sport = 'unknown' "
               "WHERE sport = 'inconnu'")


def downgrade() -> None:
    op.execute("UPDATE redline.strava_activity_intakes SET sport = 'inconnu' "
               "WHERE sport = 'unknown'")
