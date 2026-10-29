"""A planned hike is of the `hike` kind.

Revision 0004 renamed the workout `rando` to `hike` but kept the kind it was planned
under, `rest`, which the plan's validation refuses: the family of a workout is its kind.
The planned sessions are corrected; the plan revisions themselves are not rewritten.

The downgrade leaves them as they are: no earlier revision accepts a hike planned as a
rest.

Revision ID: 20261029_0013
Revises: 20261029_0012
"""

from __future__ import annotations

from alembic import op

revision = "20261029_0013"
down_revision = "20261029_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE redline.planned_sessions SET kind = 'hike' "
               "WHERE workout = 'hike' AND kind <> 'hike'")


def downgrade() -> None:
    pass
