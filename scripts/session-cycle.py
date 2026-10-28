#!/usr/bin/env python3
"""Pilote le cycle tolérant à l'oubli d'une séance.

`start` est un raccourci facultatif. Une observation Strava datée reconstruit le
départ et la fin même si l'athlète n'a rien déclaré avant de partir.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import (  # noqa: E402
    commands,
    entrypoint,
    session_cycle,
    weekly_plan,
)
from scripts.storage import Database, OperationalStore, StorageError  # noqa: E402


def _store(url: str) -> OperationalStore:
    if not url:
        raise StorageError("DATABASE_URL obligatoire")
    return OperationalStore(Database(url, application_name="session-cycle"))


def _stage_or_apply(store: OperationalStore, command_key: str, kind: str,
                    payload: dict, direct) -> str:
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if turn_id:
        store.stage_command(turn_id, command_key, kind, payload)
        return "préparé pour le tour"
    direct()
    return "enregistré"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    sub = parser.add_subparsers(dest="command", required=True)

    pending = sub.add_parser("pending")
    pending.add_argument("--week", default=weekly_plan.current_week())

    start = sub.add_parser("start")
    start.add_argument("--session-key", required=True)

    match = sub.add_parser("match")
    match.add_argument("--session-key", required=True)
    match.add_argument("--strava", type=int, required=True)

    reconcile = sub.add_parser("reconcile-strava")
    reconcile.add_argument("--days", type=int, default=30, choices=range(1, 366))
    reconcile.add_argument("--limit", type=int, default=100, choices=range(1, 201))
    reconcile.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        store = _store(args.database_url.strip())
        if args.command == "pending":
            print(json.dumps({
                "cycles": store.session_cycle_status(args.week),
                "unmatched_strava": store.unmatched_session_activities(),
                "strava_intakes": store.strava_intakes(unresolved_only=True),
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "reconcile-strava":
            from scripts import strava
            after = dt.datetime.now(session_cycle.LOCAL_TIMEZONE) - dt.timedelta(days=args.days)
            rows = strava.call("/athlete/activities", {
                "after": int(after.timestamp()), "per_page": args.limit})
            results = []
            for summary in rows:
                activity_id = int(summary["id"])
                distilled = strava.distill_activity(
                    strava.call(f"/activities/{activity_id}"))
                if args.dry_run:
                    results.append({"activity_id": activity_id, "status": "a_capturer",
                                    "depart_local": distilled.get("depart_local"),
                                    "sport": distilled.get("sport")})
                else:
                    results.append(store.ingest_strava_activity(
                        distilled, actor="session-cycle-reconcile"))
            print(json.dumps({"after": after.date().isoformat(), "results": results},
                             ensure_ascii=False, indent=2))
            return 0

        now = dt.datetime.now(session_cycle.LOCAL_TIMEZONE)
        if args.command == "start":
            payload = {"session_key": args.session_key, "declared_at": now}
            state = _stage_or_apply(
                store, f"session-start:{args.session_key}", commands.SESSION_START,
                payload, lambda: store.declare_session_start(payload, actor="session-cycle"))
            print(f"Départ facultatif {state}.")
        elif args.command == "match":
            payload = {"session_key": args.session_key,
                       "strava_activity_id": args.strava}
            state = _stage_or_apply(
                store, f"session-match:{args.strava}:{args.session_key}",
                commands.SESSION_MATCH, payload,
                lambda: store.match_session(args.session_key, args.strava,
                                            actor="session-cycle"))
            print(f"Appariement {state}.")
        return 0
    except (StorageError, ValueError) as exc:
        print(f"cycle non écrit : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    entrypoint.run(main)
