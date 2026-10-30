#!/usr/bin/env python3
"""Links an existing Messenger observation to its Strava activity.

The fact keeps its original idempotency key; only the missing external identifier is
added. The key is read with `redline fact-read observations`.
"""

from __future__ import annotations

import argparse
import os
import sys

from redline.coach.fr import ledger as wording
from redline.model import (
    commands,
    entrypoint,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="the messenger:... key of the observation")
    parser.add_argument("--strava", required=True, type=int, help="the exact Strava id")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL is required")

    from redline.storage import Database, OperationalStore, StorageError

    store = OperationalStore(Database(args.database_url, application_name="ledger-link"))
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if turn_id:
        payload = {"idempotency_key": args.key, "strava_activity_id": args.strava}
        store.stage_command(
            turn_id, f"activity-link:{args.key}:{args.strava}",
            commands.ACTIVITY_LINK, payload)
        print(wording.LINK_STAGED.format(key=args.key, strava=args.strava))
        return 0
    try:
        store.link_activity(args.key, args.strava, actor="ledger-link")
    except StorageError as exc:
        print(f"link refused: {exc}", file=sys.stderr)
        return 1
    print(wording.LINK_RECORDED.format(key=args.key, strava=args.strava))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
