#!/usr/bin/env python3
"""Completes a pending measurement by stable identity, without editing a projection.

`--hr-doubt no_sensor|context --cause "<their words>"` flags an already known
average HR as doubtful: without an armband it counts nowhere; with a context (meal,
illness) it leaves the pace-matched HR comparisons, hence the gate.

Pain is completed per zone: `--metric pain --zone genou --value 2`. `--zone` may be
omitted when the observation carries a single zone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from redline.coach.fr import ledger as wording
from redline.model import (
    commands,
    entrypoint,
)
from redline.model.errors import InputError
from redline.model.vocabulary import HrDoubt

METRICS = ("rpe", "pain", "enjoyment", "distance_km", "duration_s", "pace_s_per_km",
           "hr_avg_bpm", "hr_max_bpm", "cadence_spm", "elevation_gain_m",
           "elevation_loss_m", "wbgt_c")


def resolve_key(store, key: str, date: str, kind: str) -> str:
    """The key of the targeted observation, by stable identity OR by its date.

    `--key` alone required an identifier that nothing published: invented at `ledger-add`
    time, it survived only in the session's memory. A date can be read from the ledger.
    """
    if key:
        return key
    candidates = [row for row in store.observation_rows()
                 if row["occurred_on"].isoformat() == date
                 and (not kind or row["kind"] == kind)]
    if not candidates:
        raise InputError(f"no observation on {date}"
                         + (f" of type {kind}" if kind else "")
                         + " -- check with `redline fact-read observations`")
    if len(candidates) > 1:
        details = " · ".join(f"{row["kind"]} (key={row["idempotency_key"]})"
                             for row in candidates)
        raise InputError(f"{len(candidates)} observations on {date}: {details}. "
                         "Give --type, or pass --key.")
    return candidates[0]["idempotency_key"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="",
                        help="the observation key (see `redline fact-read observations`)")
    parser.add_argument("--date", default="",
                        help="YYYY-MM-DD -- an alternative to --key, resolved on the ledger")
    parser.add_argument("--type", dest="kind", default="",
                        help="settles the ambiguity when a date carries several rows")
    parser.add_argument("--metric", choices=METRICS)
    parser.add_argument("--zone", default=None,
                        help="the monitored zone, for --metric pain")
    parser.add_argument("--hr-doubt", dest="hr_doubt", choices=tuple(HrDoubt),
                        help="declares the observation's known average heart rate doubtful")
    parser.add_argument("--cause", default="",
                        help="the cause the athlete declared, in their words")
    values = parser.add_mutually_exclusive_group()
    values.add_argument("--value", type=float)
    values.add_argument("--unavailable", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if not args.database_url:
        parser.error("DATABASE_URL is required")
    from redline.storage import Database, OperationalStore, StorageError

    if not args.key and not args.date:
        parser.error("--key or --date is required (see `redline fact-read observations`)")
    if args.hr_doubt:
        if args.metric or args.value is not None or args.unavailable:
            parser.error("--hr-doubt is passed alone, without --metric or a value")
        if not args.cause.strip():
            parser.error("--hr-doubt requires --cause: the reason the athlete gave")
    elif not args.metric or (args.value is None and not args.unavailable):
        parser.error("--metric and (--value or --unavailable) are required")
    if args.zone is not None and args.metric != "pain":
        parser.error("--zone only applies to --metric pain")

    store = OperationalStore(Database(args.database_url, application_name="ledger-complete"))
    # Resolved BEFORE staging: a turn command prepared on a nonexistent key only fails
    # at validation, well after the coach's reply.
    key = resolve_key(store, args.key, args.date, args.kind)
    # Checked NOW, not at the turn boundary: once the reply is written the coach can no
    # longer correct itself and the whole turn falls. Here it reads the error, retries
    # with the right key, and its message goes out intact.
    observation_id = store.observation_id(key)
    if observation_id is None:
        recent = " · ".join(
            f"{row["occurred_on"].isoformat()} {row["kind"]} (key={row["idempotency_key"]})"
            for row in store.observation_rows()[:5])
        raise InputError(f"unknown observation: {key}. Recent -- {recent}")
    if args.hr_doubt:
        payload = {"idempotency_key": key, "reason": args.hr_doubt, "note": args.cause.strip()}
        if turn_id:
            digest = hashlib.sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
            store.stage_command(turn_id, f"hr-doubt:{key}:{digest}", commands.HR_DOUBT,
                                payload)
            print(wording.HR_DOUBT_STAGED.format(key=key, hr_doubt=args.hr_doubt))
            return 0
        try:
            store.doubt_hr(observation_id, args.hr_doubt, args.cause, actor="ledger-complete")
        except StorageError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(wording.HR_DOUBT_RECORDED.format(key=key, hr_doubt=args.hr_doubt))
        return 0
    if turn_id:
        payload = {"idempotency_key": key, "metric": args.metric,
                   "value": args.value, "unavailable": args.unavailable}
        if args.zone is not None:
            payload["zone"] = args.zone
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
        store.stage_command(turn_id, f"measurement:{key}:{args.metric}:{digest}",
                            commands.MEASUREMENT_COMPLETE, payload)
        print(wording.MEASUREMENT_STAGED.format(key=key, metric=args.metric))
        return 0
    try:
        store.complete_measurement(observation_id, args.metric, args.value, zone=args.zone,
                                   unavailable=args.unavailable, actor="ledger-complete")
    except StorageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    what = f"{args.metric} {args.zone}" if args.zone else args.metric
    print(wording.MEASUREMENT_RECORDED.format(key=key, what=what))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
