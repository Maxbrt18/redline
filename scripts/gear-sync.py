#!/usr/bin/env python3
"""Synchronise le kilométrage de toutes les chaussures Strava vers PostgreSQL."""

# ruff: noqa: E402 -- exécutable direct : la racine est amorcée avant ses imports

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import strava
from scripts.storage import Database, OperationalStore


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL absent")
    database = Database(database_url, application_name="gear-sync")
    try:
        store = OperationalStore(database)
        rows = strava.gear_rows()
        for row in rows:
            store.sync_strava_gear(row)
            print(f"{row['gear_id']} · {row['name']} · {row['distance_km']:.1f} km")
        print(f"{len(rows)} paire(s) synchronisée(s)")
    finally:
        database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
