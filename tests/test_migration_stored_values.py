"""Revision 0014 rewrites the French values stored before it; its downgrade puts them back."""

import os
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from conftest import ENGINE_ROOT
from sqlalchemy.engine import make_url

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")

BEFORE, AFTER = "20261029_0013", "20261029_0014"
RACE = "race:r1:2030-03-30"

SEED = f"""
INSERT INTO redline.weekly_plans (id, athlete_id, iso_week)
  VALUES ('00000000-0000-0000-0000-000000000001', 'athlete', '2030-W10');
INSERT INTO redline.plan_revisions (id, plan_id, revision, computed_at, reason)
  VALUES ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001',
          1, now(), 'test');
INSERT INTO redline.planned_sessions (id, revision_id, logical_key, title, scheduled_on,
                                      kind, workout, status, demanding, transition)
  VALUES ('00000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002',
          'w10-night', 'Footing', '2030-03-05', 'run', 'easy', 'planned', false, 'nocturne');
INSERT INTO redline.plan_gates (id, revision_id, logical_key, due_on, question, on_fail,
                                applies_when, holds)
  VALUES ('00000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000002',
          'g1', '2030-03-04', 'Une question datée ?', 'repos', 'avant', '["w10-night"]');
INSERT INTO redline.strava_activity_intakes (athlete_id, activity_id, sport, distilled,
                                             status, weather)
  VALUES ('athlete', 1, 'Run', '{{}}', 'captured', '{{"temp": 9, "conditions": "pluie faible"}}');
INSERT INTO redline.proactive_jobs (job_key, kind, detail, due_at)
  VALUES ('{RACE}:preuve', 'race_countdown', '{{"stage":"preuve","title":"x"}}', now());
INSERT INTO redline.proactive_messages (id, athlete_id, source_key, sent_at, kind, summary)
  VALUES ('00000000-0000-0000-0000-000000000005', 'athlete', '{RACE}:gel-v1', now(),
          'other', 'jalon');
"""


def _alembic(url, *args: str) -> None:
    """In its own process: the migration environment reconfigures logging."""
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ENGINE_ROOT, check=True,
                   capture_output=True,
                   env={**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)})


@pytest.fixture
def database():
    """A database of its own: the revision is played down and up, alone."""
    name = f"redline_migration_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f"CREATE DATABASE {name}"))
    url = make_url(URL).set(database=name)
    engine = sa.create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(sa.text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


def _rows(engine) -> dict:
    with engine.connect() as connection:
        def one(sql: str):
            return connection.execute(sa.text(sql)).scalar_one()
        return {
            "transition": one("SELECT transition FROM redline.planned_sessions"),
            "when": one("SELECT applies_when::text FROM redline.plan_gates"),
            "weather": one("SELECT weather FROM redline.strava_activity_intakes"),
            "job": one("SELECT job_key || ' ' || detail FROM redline.proactive_jobs"),
            "message": one("SELECT source_key FROM redline.proactive_messages"),
        }


def test_the_french_values_stored_before_0014_become_english_and_come_back(database):
    _alembic(database.url, "upgrade", BEFORE)
    with database.begin() as connection:
        for statement in SEED.split(";\n"):
            if statement.strip():
                connection.execute(sa.text(statement))

    _alembic(database.url, "upgrade", AFTER)
    assert _rows(database) == {
        "transition": "night",
        "when": "before",
        "weather": {"temp": 9, "weather_code": 61},
        "job": f'{RACE}:proof {{"stage":"proof","title":"x"}}',
        "message": f"{RACE}:freeze-v1",
    }

    _alembic(database.url, "downgrade", BEFORE)
    assert _rows(database) == {
        "transition": "nocturne",
        "when": "avant",
        "weather": {"temp": 9, "conditions": "pluie faible"},
        "job": f'{RACE}:preuve {{"stage":"preuve","title":"x"}}',
        "message": f"{RACE}:gel-v1",
    }
