#!/usr/bin/env python3
"""Syncs the mileage of every Strava shoe into PostgreSQL."""



from __future__ import annotations

import os

from redline.integrations import strava
from redline.model import entrypoint
from redline.model.errors import ConfigError
from redline.storage import Database, OperationalStore


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError("DATABASE_URL absent")
    database = Database(database_url, application_name="gear-sync")
    try:
        store = OperationalStore(database)
        rows = strava.gear_rows()
        for row in rows:
            store.sync_strava_gear(row)
            print(f"{row['gear_id']} · {row['name']} · {row['distance_km']:.1f} km")
        print(f"{len(rows)} pair(s) synchronised")
    finally:
        database.dispose()
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
