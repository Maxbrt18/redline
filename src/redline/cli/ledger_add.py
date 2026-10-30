#!/usr/bin/env python3
"""Adds a typed observation to PostgreSQL.

The command normalises the floats supplied by Strava, validates each option against
the contract of `model/ledger.py`, then writes the canonical observation directly.

    redline ledger-add --date YYYY-MM-DD --type EF --km 6.84 \\
        --time 0:41:56 --pace 6:08 --hr-avg 156.61 --hr-max 168 --cadence 168.88 \\
        --dplus 64.7 --dminus 64 --wbgt 20 --strava 90000000003 --note "…"

Pain is given per monitored zone: `--pain genou=2 --pain cheville=?`. With a single
zone, `--pain 2` is enough.

`--dry-run` prints the validated payload without writing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import unicodedata
from typing import Any

from redline.coach.fr import ledger as wording
from redline.model import (
    commands,
    entrypoint,
    session_cycle,
    session_kinds,
)
from redline.model import ledger as table


def option_of(item: table.ObservationInput) -> str:
    return item.name.replace("_", "-")


def _number(name: str, value: str) -> float | int:
    if name == "time":
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = 0, parts[0], parts[1]
        else:
            raise ValueError(f"invalid duration: {value}")
        return hours * 3600 + minutes * 60 + seconds
    if name == "pace":
        minute, second = (int(part) for part in value.split(":"))
        return minute * 60 + second
    number = float(value)
    return int(number) if number.is_integer() else number


def morning_trace_errors(raw: dict[str, str], pains: dict[str, str],
                         zones: list[str]) -> list[str]:
    """A `wake-trace` row carries its trace, or it is not written.

    Without `--pain` the measurement would silently be born `pending` and the athlete's
    answer would live only in the note, leaving the gate "incomplete" and prompting a
    follow-up on something already answered. The trace slug is accepted as is and converted here
    (none 0, very-light 0.5, light 1, clear 2).
    "?" is refused too: a wake-up check without an answer has no row, and the catch-up
    must not turn answers already given into `unavailable`.
    """
    if raw.get("type") != session_kinds.WAKE_TRACE:
        return []
    if not zones:
        return ["wake reading without a monitored zone: there is no trace to record"]
    problems = []
    for zone in zones:
        given = pains.get(zone, "")
        word = unicodedata.normalize("NFKD", given.lower()).encode("ascii", "ignore").decode()
        word = "-".join(word.split())
        if word in session_cycle.MORNING_TRACE_VALUES:
            pains[zone] = str(session_cycle.MORNING_TRACE_VALUES[word])
            continue
        if given and given != table.UNKNOWN:
            continue
        problems.append(
            f"wake reading without a trace for {zone}: pass --pain {zone}=none, very-light, "
            "light or clear (0, 0.5, 1 or 2). If the athlete named no trace, write no row "
            "and ask for it.")
    return problems


def _write_database(url: str, raw: dict[str, str], pains: dict[str, str], key: str, *,
                    start_local: str = "", session_key: str = "") -> str:
    if not key:
        raise table.LedgerError(
            "--idempotency-key is required without a Strava id (the stable identity of the "
            "fact)")
    kind = raw["type"]
    measurements: dict[str, dict[str, Any]] = {}
    for item in table.INPUTS:
        if not item.metric:
            continue
        value = raw.get(item.name, "")
        if value == table.UNKNOWN:
            measurements[item.metric] = {"state": "unavailable"}
        elif value:
            measurements[item.metric] = {
                "state": "known", "value": _number(item.name, value)}
        else:
            required = item.name in table.required_columns(kind) or (
                bool(raw.get("strava")) and item.name in ("dplus", "dminus"))
            measurements[item.metric] = {
                "state": "pending" if required else "not_applicable"}
    payload = {
        "occurred_on": dt.date.fromisoformat(raw["date"]), "kind": kind,
        "idempotency_key": key, "strava_activity_id": raw.get("strava") or None,
        "routine": raw.get("routine") or None,
        "note": raw.get("note", ""), "source": "ledger-add", "measurements": measurements,
        "pains": {zone: table.pain_reading(value) for zone, value in pains.items()},
    }
    if start_local:
        from redline.model import session_cycle
        started_at = session_cycle.local_wall_clock(start_local)
        if started_at.date() != payload["occurred_on"]:
            raise table.LedgerError("--start-local does not fall on the day of --date")
        payload["started_at"] = started_at
    elif session_key:
        raise table.LedgerError("--session-key requires --start-local")
    if session_key:
        payload["session_key"] = session_key
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    from redline.storage import Database, OperationalStore, StorageError

    store = OperationalStore(Database(url, application_name="ledger-add"))
    if turn_id and store.observation_id(key) is not None:
        raise table.LedgerError(
            f"observation already recorded: {key}. Read redline fact-read observations; "
            "complete only the missing measurements with redline ledger-complete --key "
            f"{key} --metric <metric> --value <value>. Do not recreate the session and do "
            "not change its key.")
    if turn_id:
        store.stage_command(
            turn_id, f"observation:{key}", commands.OBSERVATION_ADD, payload)
        return key
    try:
        oid = store.add_observation(payload, actor="ledger-add")
    except StorageError as exc:
        raise table.LedgerError(str(exc)) from exc
    return str(oid)


def _active_zones(url: str) -> list[str]:
    from redline.storage import Database, OperationalStore

    store = OperationalStore(Database(url, application_name="ledger-add"))
    return [zone["slug"] for zone in store.pain_zones() if zone["active"]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Empty = do not pass the option. `?` = asked for, then lost (rpe, pain, "
               "enjoyment only).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the validated row, write nothing")
    parser.add_argument("--idempotency-key", default="",
                        help="stable key, required without Strava, e.g. messenger:<mid>:reading")
    parser.add_argument("--start-local", default="",
                        help="local ISO time as returned by redline strava (`start_local`)")
    parser.add_argument("--session-key", default="",
                        help="explicit pairing, e.g. 2030-W34:w34-fri-easy")
    for item in table.INPUTS:
        parser.add_argument(f"--{option_of(item)}", default="",
                            help=f"field {item.name!r}"
                                 + (f" ({item.note})" if item.note else ""))
    parser.add_argument("--pain", action="append", default=[], metavar="ZONE=VALUE",
                        help=f"pain of a monitored zone ({table.PAIN_NOTE}); repeatable, "
                             "`?` = asked for, then lost")
    args = parser.parse_args()

    corrected, raw_values = [], {}
    for item in table.INPUTS:
        raw = str(getattr(args, option_of(item).replace("-", "_")) or "").strip()
        if not raw:
            continue
        good = table.canonical_value(item.name, raw)
        if good != raw:
            corrected.append(wording.ROUNDED.format(name=item.name, raw=raw, good=good))
        raw_values[item.name] = good

    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    zones: list[str] = []
    if args.pain or raw_values.get("type") == session_kinds.WAKE_TRACE:
        if not database_url:
            print("DATABASE_URL is required: the monitored zones live in the database",
                  file=sys.stderr)
            return 2
        zones = _active_zones(database_url)
    try:
        pains = table.parse_pains([value.strip() for value in args.pain], zones)
    except table.LedgerError as exc:
        print(f"observation refused: {exc}", file=sys.stderr)
        return 1
    problems = morning_trace_errors(raw_values, pains, zones)
    if raw_values.get("routine") and raw_values.get("type") != session_kinds.STRENGTH:
        problems.append(f"--routine only applies to --type {session_kinds.STRENGTH}")
    problems += table.INPUT_SCHEMA.errors(raw_values, raw_values.get("date", "observation"))
    for zone, value in pains.items():
        try:
            table.pain_reading(value)
        except table.LedgerError as exc:
            problems.append(f"{zone} : {exc}")
    if problems:
        print("\n  - ".join(["observation refused:", *problems]), file=sys.stderr)
        return 1

    for change in corrected:
        print(change, file=sys.stderr)
    if args.dry_run:
        print({**raw_values, **({"pain": pains} if pains else {})})
        return 0

    if not database_url:
        print("DATABASE_URL is required: there is no fallback ledger", file=sys.stderr)
        return 2
    key = args.idempotency_key.strip()
    if raw_values.get("strava"):
        key = f"strava:{raw_values['strava']}"
    try:
        oid = _write_database(database_url, raw_values, pains, key,
                              start_local=args.start_local.strip(),
                              session_key=args.session_key.strip())
    except (ValueError, table.LedgerError) as exc:
        print(f"observation not written: {exc}", file=sys.stderr)
        return 1
    state = wording.STAGED if os.environ.get("REDLINE_TURN_ID") else wording.RECORDED
    print(wording.OBSERVATION_WRITTEN.format(date=raw_values["date"], type=raw_values["type"],
                                             oid=oid, state=state))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
