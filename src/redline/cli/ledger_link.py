#!/usr/bin/env python3
"""Links an existing Messenger observation to its Strava activity.

The fact keeps its original idempotency key; only the missing external identifier is
added. The key is read with `redline fact-read observations`.
"""

from __future__ import annotations

import argparse

from redline.coach.fr import ledger as wording
from redline.model import (
    agent_input,
    commands,
    entrypoint,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="the messenger:... key of the observation")
    parser.add_argument("--strava", required=True, type=int, help="the exact Strava id")
    args = parser.parse_args()
    from redline.storage import open_store

    payload = {"idempotency_key": args.key, "strava_activity_id": args.strava}
    submitted = open_store("ledger-link").submit(
        commands.ACTIVITY_LINK, payload, command_key=f"activity-link:{args.key}:{args.strava}",
        turn_id=agent_input.current_turn(), actor="ledger-link")
    line = wording.LINK_STAGED if submitted.staged else wording.LINK_RECORDED
    print(line.format(key=args.key, strava=args.strava))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
