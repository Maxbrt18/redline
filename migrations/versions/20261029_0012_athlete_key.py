"""The athlete's key becomes `athlete`, the same in every instance.

The engine serves one athlete; its key named a person, now it names the role. The single
row of `athletes` is renamed, whatever its key, with every `athlete_id` that references
it. The foreign keys are not `ON UPDATE CASCADE` and several tables refuse updates by
trigger: both are lifted for the rewrite, then put back as they were.

The downgrade renames it `sam`, the key of the test fixtures.

Revision ID: 20261029_0012
Revises: 20261029_0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261029_0012"
down_revision = "20261029_0011"
branch_labels = None
depends_on = None

KEY = "athlete"
PREVIOUS = "sam"


def _rename(target: str) -> None:
    bind = op.get_bind()
    current = bind.execute(sa.text("SELECT id FROM redline.athletes")).scalars().all()
    if len(current) != 1:
        raise RuntimeError(f"un seul athlète attendu, trouvé {len(current)}")
    source = current[0]
    if source == target:
        return
    tables = bind.execute(sa.text(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'redline' AND column_name = 'athlete_id' ORDER BY 1"
    )).scalars().all()
    # Every foreign key with an `athlete_id` column, child to athlete or child to child.
    keys = bind.execute(sa.text("""
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE contype = 'f' AND connamespace = 'redline'::regnamespace
          AND EXISTS (SELECT 1 FROM unnest(conkey) AS k(n)
                      JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = k.n
                      WHERE a.attname = 'athlete_id')
    """)).all()
    for table, name, _ in keys:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
    for table in tables:
        op.execute(f"ALTER TABLE redline.{table} DISABLE TRIGGER USER")
        bind.execute(sa.text(f"UPDATE redline.{table} SET athlete_id = :target "
                             "WHERE athlete_id = :source"),
                     {"target": target, "source": source})
        op.execute(f"ALTER TABLE redline.{table} ENABLE TRIGGER USER")
    bind.execute(sa.text("UPDATE redline.athletes SET id = :target WHERE id = :source"),
                 {"target": target, "source": source})
    for table, name, definition in keys:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}")


def upgrade() -> None:
    _rename(KEY)


def downgrade() -> None:
    _rename(PREVIOUS)
