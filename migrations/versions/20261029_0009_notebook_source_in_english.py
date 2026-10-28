"""What the notebook writes is attributed to `notebook`, not `carnet`.

`observations.source` and the idempotency key of a wake-up declared in the notebook
(`carnet:reveil:<day>` becomes `notebook:wake-trace:<day>`): the notebook looks that key
up to complete the other zones of the same morning. The audit trail keeps its actors:
it is append-only history.

Revision ID: 20261029_0009
Revises: 20261029_0008
"""

from __future__ import annotations

from alembic import op

revision = "20261029_0009"
down_revision = "20261029_0008"
branch_labels = None
depends_on = None

SOURCE = ("carnet", "notebook")
KEY = ("carnet:reveil:", "notebook:wake-trace:")


def _move(source: tuple[str, str], key: tuple[str, str]) -> None:
    op.execute(f"UPDATE redline.observations SET source = '{source[1]}' "
               f"WHERE source = '{source[0]}'")
    op.execute(f"UPDATE redline.observations "
               f"SET idempotency_key = '{key[1]}' || substr(idempotency_key, {len(key[0]) + 1}) "
               f"WHERE idempotency_key LIKE '{key[0]}%'")


def upgrade() -> None:
    _move(SOURCE, KEY)


def downgrade() -> None:
    _move(SOURCE[::-1], KEY[::-1])
