#!/usr/bin/env python3
"""Inspects or reopens one exact periodic review after an operational incident.

Operator command only: it stays out of `deploy/agent-cli/agent-cli-tools.txt`, so the sandboxed
coach cannot replay an already acknowledged initiative itself.
"""

from __future__ import annotations

import argparse
import re
import time

from redline.model import entrypoint, iso_week
from redline.model.errors import InputError
from redline.model.vocabulary import FINISHED_JOBS
from redline.service.postgres_state import PostgresStateStore
from redline.storage.database import from_environment

PERIODIC_KEY = re.compile(
    rf"^((?:weekly|weekly-repair):{iso_week.PATTERN}|"
    r"monthly:20\d{2}-(?:0[1-9]|1[0-2]))$")


def _state() -> PostgresStateStore:
    return PostgresStateStore(from_environment("proactive-job"))


def _row(state: PostgresStateStore, key: str) -> dict:
    try:
        return next(job for job in state.proactive.jobs() if job["id"] == key)
    except StopIteration:
        raise InputError(f"unknown job: {key}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "reopen"))
    parser.add_argument("--job-key", required=True)
    parser.add_argument("--valid-hours", type=float, default=24.0)
    args = parser.parse_args()
    if not PERIODIC_KEY.fullmatch(args.job_key):
        parser.error("--job-key must name exactly weekly:YYYY-WNN, weekly-repair:YYYY-WNN or "
                     "monthly:YYYY-MM")
    if not 0 < args.valid_hours <= 48:
        parser.error("--valid-hours must be in ]0, 48]")

    state = _state()
    before = _row(state, args.job_key)
    if args.command == "reopen":
        if before["status"] not in FINISHED_JOBS:
            raise InputError(
                f"{args.job_key}: reopening refused, status {before["status"]}")
        now = time.time()
        state.proactive.reopen_job(
            args.job_key, due=now, expire_at=now + args.valid_hours * 3600)
    current = _row(state, args.job_key)
    cause = f" cause={current['last_error']}" if current.get("last_error") else ""
    print(
        f"{current["id"]}: status={current["status"]} attempts={current["attempts"]}{cause}")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
