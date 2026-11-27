"""Migrations 0018 and 0019 retire the skeleton's and the taper's keys from the races
already written, on a database of their own."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

import pytest
import sqlalchemy as sa
from test_race_migration import _alembic

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL absent")

RETIRED = {"peak_volume_km": 50, "peak_long_run_km": 20, "quality_per_week": 2,
           "families": ["threshold"], "skeleton": {"candidate": "balanced"}}
KEPT = {"distance_km": 21.1, "terrain": "road", "stop_minutes": 2}


@pytest.fixture
def before_cycles(request):
    """A database migrated to 0017, dropped afterwards."""
    from conftest import ADMIN_URL
    base = sa.engine.make_url(ADMIN_URL)
    admin = sa.create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = f"redline_cycles_{uuid.uuid4().hex[:12]}"
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        _alembic(url, "upgrade", "0017")
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"), {"name": name})
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        admin.dispose()


def test_the_skeleton_s_keys_leave_the_race_and_its_other_keys_stay(before_cycles):
    engine = sa.create_engine(before_cycles)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('athlete') "
                                   "ON CONFLICT DO NOTHING"))
        connection.execute(sa.text(
            "INSERT INTO redline.deadlines(id, athlete_id, deadline_key, due_on, item, "
            "fact_owner, status, kind, priority, race_state, context) VALUES (:id, "
            "'athlete', 'half', :due_on, 'Semi', 'test', 'scheduled', 'race', 'a', 'active', "
            "CAST(:context AS jsonb))"),
            {"id": uuid.uuid4(), "due_on": dt.date(2031, 4, 6),
             "context": json.dumps({**KEPT, **RETIRED})})
    _alembic(before_cycles, "upgrade", "0018")
    with engine.connect() as connection:
        context = connection.execute(sa.text("SELECT context FROM redline.deadlines")).scalar_one()
    assert context == KEPT
    _alembic(before_cycles, "downgrade", "0017")
    with engine.connect() as connection:
        tables = connection.execute(sa.text(
            "SELECT count(*) FROM pg_tables WHERE tablename LIKE 'cycle_%'")).scalar_one()
    engine.dispose()
    assert tables == 0


def test_the_taper_s_keys_leave_the_race_from_0019(before_cycles):
    engine = sa.create_engine(before_cycles)
    _alembic(before_cycles, "upgrade", "0018")
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO redline.athletes(id) VALUES ('athlete') "
                                   "ON CONFLICT DO NOTHING"))
        connection.execute(sa.text(
            "INSERT INTO redline.deadlines(id, athlete_id, deadline_key, due_on, item, "
            "fact_owner, status, kind, priority, race_state, context) VALUES (:id, "
            "'athlete', 'half', :due_on, 'Semi', 'test', 'scheduled', 'race', 'a', 'active', "
            "CAST(:context AS jsonb))"),
            {"id": uuid.uuid4(), "due_on": dt.date(2031, 4, 6),
             "context": json.dumps({"distance_km": 21.1, "taper_weeks": 2,
                                    "taper_cut_min_pct": 40, "taper_cut_max_pct": 60})})
    _alembic(before_cycles, "upgrade", "0019")
    with engine.connect() as connection:
        context = connection.execute(sa.text("SELECT context FROM redline.deadlines")).scalar_one()
    assert context == {"distance_km": 21.1}
    _alembic(before_cycles, "downgrade", "0018")
    engine.dispose()
