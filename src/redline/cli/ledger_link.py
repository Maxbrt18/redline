#!/usr/bin/env python3
"""Rattache une observation Messenger existante à son activité Strava.

Le fait garde sa clé d'idempotence d'origine ; seul l'identifiant externe manquant
s'ajoute. La clé se lit avec `scripts/fact-read.py observations`.
"""

from __future__ import annotations

import argparse
import os
import sys

from redline.model import (
    commands,
    entrypoint,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="clé messenger:… de l'observation")
    parser.add_argument("--strava", required=True, type=int, help="identifiant Strava exact")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL obligatoire")

    from redline.storage import Database, OperationalStore, StorageError

    store = OperationalStore(Database(args.database_url, application_name="ledger-link"))
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if turn_id:
        payload = {"idempotency_key": args.key, "strava_activity_id": args.strava}
        store.stage_command(
            turn_id, f"activity-link:{args.key}:{args.strava}",
            commands.ACTIVITY_LINK, payload)
        print(f"{args.key} -> Strava {args.strava} : rattachement préparé pour le tour.")
        return 0
    try:
        store.link_activity(args.key, args.strava, actor="ledger-link")
    except StorageError as exc:
        print(f"rattachement refusé : {exc}", file=sys.stderr)
        return 1
    print(f"{args.key} -> Strava {args.strava} : observation rattachée.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
