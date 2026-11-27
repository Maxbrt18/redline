"""Migration 0015 takes up the races already written, on a database of their own."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from conftest import ADMIN_URL, ENGINE_ROOT

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL absent")

TODAY = dt.date.today()


def _alembic(url: str, *args: str) -> None:
    done = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ENGINE_ROOT,
                          env={**os.environ, "DATABASE_URL": url}, text=True,
                          capture_output=True, check=False)
    assert done.returncode == 0, done.stderr or done.stdout


@pytest.fixture
def before_races():
    """A database migrated to 0014, dropped afterwards."""
    base = sa.engine.make_url(ADMIN_URL)
    admin = sa.create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = f"redline_races_{uuid.uuid4().hex[:12]}"
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        _alembic(url, "upgrade", "0014")
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"), {"name": name})
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


def _deadline(key, due_on, status, kind="race"):
    return {"id": uuid.uuid4(), "athlete_id": "athlete", "deadline_key": key, "due_on": due_on,
            "item": key, "fact_owner": "athlete/goals.md", "status": status, "kind": kind}


def test_a_race_done_was_run_and_the_nearest_race_ahead_is_the_goal(before_races):
    engine = sa.create_engine(before_races)
    rows = [_deadline("ran", TODAY - dt.timedelta(days=60), "done"),
            _deadline("missed", TODAY - dt.timedelta(days=3), "scheduled"),
            _deadline("goal", TODAY + dt.timedelta(days=90), "scheduled"),
            _deadline("later", TODAY + dt.timedelta(days=200), "scheduled"),
            _deadline("appointment", TODAY + dt.timedelta(days=10), "todo", kind="medical")]
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('athlete') "
                                   "ON CONFLICT DO NOTHING"))
        connection.execute(sa.text(
            "INSERT INTO redline.deadlines(id, athlete_id, deadline_key, due_on, item, "
            "fact_owner, status, kind) VALUES (:id, :athlete_id, :deadline_key, :due_on, "
            ":item, :fact_owner, :status, :kind)"), rows)
    _alembic(before_races, "upgrade", "0015")
    with engine.connect() as connection:
        found = {row.deadline_key: (row.priority, row.race_state) for row in connection.execute(
            sa.text("SELECT deadline_key, priority, race_state FROM redline.deadlines"))}
    engine.dispose()
    assert found == {"ran": ("a", "run"), "missed": ("b", "active"), "goal": ("a", "active"),
                     "later": ("b", "active"), "appointment": (None, None)}
    _alembic(before_races, "downgrade", "0014")
