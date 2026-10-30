"""Creates the whole operational storage in a single revision.

This revision is a FROZEN SNAPSHOT: `0001_baseline.sql` is the output of `pg_dump
--schema-only`, and no later revision rewrites it. The functions and triggers carry the
guarantees a declaration cannot express: immutable known measurements, contiguous
append-only plan revisions, immutable audit and proactive messages, a frozen snapshot of
a delivered session. Privileges are installed only if the `redline_owner` / `redline_app`
roles already exist.

Revision ID: 0001
Revises: None
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

SCHEMA = Path(__file__).with_suffix(".sql")

# Default privileges grant DML on future tables. Append-only tables are then reduced to
# exactly what the service needs.
PRIVILEGES = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'redline_app') THEN
    GRANT USAGE ON SCHEMA redline, public TO redline_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA redline TO redline_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA redline TO redline_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE redline_owner IN SCHEMA redline
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO redline_app;
    ALTER DEFAULT PRIVILEGES FOR ROLE redline_owner IN SCHEMA redline
      GRANT USAGE, SELECT ON SEQUENCES TO redline_app;
    REVOKE UPDATE, DELETE ON redline.audit_events FROM redline_app;
    REVOKE UPDATE, DELETE ON redline.notebook_entries FROM redline_app;
    REVOKE UPDATE, DELETE ON redline.permit_choices FROM redline_app;
    REVOKE UPDATE, DELETE ON redline.proactive_messages FROM redline_app;
    REVOKE DELETE ON redline.projection_states FROM redline_app;
  END IF;
END $$
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS redline")
    # The raw cursor runs the whole script as is: the dump holds several statements and
    # literal `%` signs, which a parametrized execution would refuse.
    connection.connection.cursor().execute(SCHEMA.read_text(encoding="utf-8"))
    connection.exec_driver_sql("INSERT INTO redline.athletes (id) VALUES ('athlete')")
    connection.exec_driver_sql(PRIVILEGES)


def downgrade() -> None:
    op.execute("DROP SCHEMA redline CASCADE")
