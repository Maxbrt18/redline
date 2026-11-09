"""Syncs the mileage of every Strava shoe into PostgreSQL."""

from __future__ import annotations

from redline.integrations import strava
from redline.model import entrypoint
from redline.storage import OperationalStore
from redline.storage.database import from_environment


def main() -> int:
    database = from_environment("gear-sync")
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
