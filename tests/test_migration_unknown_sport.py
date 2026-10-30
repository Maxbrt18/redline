"""Revision 0015 renames the sport of the failed captures stored before it."""

import pytest
import sqlalchemy as sa
from test_migration_stored_values import URL, _alembic, database  # noqa: F401

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL absent")

BEFORE, AFTER = "20261029_0014", "20261030_0015"


def _sports(engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.execute(sa.text(
            "SELECT sport FROM redline.strava_activity_intakes ORDER BY activity_id")).scalars())


def test_a_failed_capture_gets_the_unknown_sport_and_gets_it_back(database):  # noqa: F811
    _alembic(database.url, "upgrade", BEFORE)
    with database.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO redline.strava_activity_intakes (athlete_id, activity_id, sport, "
            "distilled, status) VALUES ('athlete', 1, 'inconnu', '{}', 'failed'), "
            "('athlete', 2, 'Run', '{}', 'captured')"))

    _alembic(database.url, "upgrade", AFTER)
    assert _sports(database) == ["unknown", "Run"]

    _alembic(database.url, "downgrade", BEFORE)
    assert _sports(database) == ["inconnu", "Run"]
