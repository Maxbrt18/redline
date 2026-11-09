#!/usr/bin/env python3
"""Drives the session cycle, tolerant of a forgotten session.

`start` is an optional shortcut. A dated Strava observation rebuilds the start and
the end even if the athlete declared nothing before leaving.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from redline.coach.fr import ledger as wording
from redline.model import (
    agent_input,
    commands,
    entrypoint,
    iso_week,
    local_time,
)
from redline.storage import OperationalStore, open_store

ACTOR = "session-cycle"


def _submit(store: OperationalStore, command_key: str, kind: str, payload: dict) -> str:
    submitted = store.submit(kind, payload, command_key=command_key,
                             turn_id=agent_input.current_turn(), actor=ACTOR)
    return wording.STAGED_MASCULINE if submitted.staged else wording.RECORDED_MASCULINE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pending = sub.add_parser("pending")
    pending.add_argument("--week", default=iso_week.of(dt.date.today()))

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

    store = open_store(ACTOR)
    if args.command == "pending":
        print(json.dumps({
            "cycles": store.session_cycle_status(args.week),
            "unmatched_strava": store.unmatched_session_activities(),
            "strava_intakes": store.strava_intakes(unresolved_only=True),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "reconcile-strava":
        from redline.integrations import strava
        after = dt.datetime.now(local_time.zone()) - dt.timedelta(days=args.days)
        rows = strava.call("/athlete/activities", {
            "after": int(after.timestamp()), "per_page": args.limit})
        results = []
        for summary in rows:
            activity_id = int(summary["id"])
            activity = strava.parse_activity(
                strava.call(f"/activities/{activity_id}"))
            if args.dry_run:
                results.append({"activity_id": activity_id, "status": "to_capture",
                                "start_local": activity.start_local,
                                "sport": activity.sport})
            else:
                results.append(store.ingest_strava_activity(
                    activity, actor="session-cycle-reconcile"))
        print(json.dumps({"after": after.date().isoformat(), "results": results},
                         ensure_ascii=False, indent=2))
        return 0

    now = dt.datetime.now(local_time.zone())
    if args.command == "start":
        payload = {"session_key": args.session_key, "declared_at": now}
        state = _submit(store, f"session-start:{args.session_key}", commands.SESSION_START,
                        payload)
        print(wording.START_WRITTEN.format(state=state))
    elif args.command == "match":
        payload = {"session_key": args.session_key,
                   "strava_activity_id": args.strava}
        state = _submit(store, f"session-match:{args.strava}:{args.session_key}",
                        commands.SESSION_MATCH, payload)
        print(wording.MATCH_WRITTEN.format(state=state))
    return 0



if __name__ == "__main__":
    entrypoint.run(main)
