"""The ledger cells the athlete declares get English names.

A cell key is `<date>:<kind>:<column>`: `RPE` becomes `rpe`, `Plaisir` `enjoyment` and
`Dlr:<zone>` `pain:<zone>`. They are stored in the "already asked" markers
(`proactive_markers`, `asked:<key>`) and in the detail of the ledger claims still to
run (`proactive_jobs`). `proactive_messages` keeps what it recorded: it is
append-only history.

Run with the service stopped: a claim enqueued before the upgrade would carry old keys.

Revision ID: 20261029_0011
Revises: 20261029_0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_0011"
down_revision = "20261029_0010"
branch_labels = None
depends_on = None

COLUMNS = {"RPE": "rpe", "Plaisir": "enjoyment", "Dlr": "pain"}


def _rename(mapping: dict[str, str]) -> None:
    # The patterns travel as parameters: `:RPE` written in the SQL would read as one.
    for old, new in mapping.items():
        op.execute(sa.text(
            "UPDATE redline.proactive_markers "
            "SET marker_key = regexp_replace(marker_key, :column, :renamed) "
            "WHERE marker_key LIKE 'asked:%' AND marker_key ~ :column"
        ).bindparams(column=f":{old}($|:)", renamed=f":{new}\\1"))
        op.execute(sa.text(
            "UPDATE redline.proactive_jobs "
            "SET detail = regexp_replace(detail, :column, :renamed, 'g') "
            "WHERE kind = 'ledger_incomplete' AND status IN ('pending', 'running')"
        ).bindparams(column=f'([":]){old}([":])', renamed=f"\\1{new}\\2"))


def upgrade() -> None:
    _rename(COLUMNS)


def downgrade() -> None:
    _rename({new: old for old, new in COLUMNS.items()})
