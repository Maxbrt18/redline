#!/usr/bin/env python3
"""Completes a pending measurement by stable identity, without editing a projection.

`--fc-douteuse no_sensor|context --cause "<their words>"` flags an already known
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
    candidats = [row for row in store.observation_rows()
                 if row["occurred_on"].isoformat() == date
                 and (not kind or row["kind"] == kind)]
    if not candidats:
        raise InputError(f"aucune observation le {date}"
                         + (f" de type {kind}" if kind else "")
                         + " — vérifie avec `fact-read.py observations`")
    if len(candidats) > 1:
        details = " · ".join(f"{row['kind']} (clé={row['idempotency_key']})"
                             for row in candidats)
        raise InputError(f"{len(candidats)} observations le {date} : {details}. "
                         "Précise --type, ou passe --key.")
    return candidats[0]["idempotency_key"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="",
                        help="clé de l'observation (cf. `fact-read.py observations`)")
    parser.add_argument("--date", default="",
                        help="AAAA-MM-JJ — alternative à --key, résolue sur le registre")
    parser.add_argument("--type", dest="kind", default="",
                        help="lève l'ambiguïté quand une date porte plusieurs lignes")
    parser.add_argument("--metric", choices=METRICS)
    parser.add_argument("--zone", default=None,
                        help="la zone surveillée, pour --metric pain")
    parser.add_argument("--fc-douteuse", dest="hr_doubt", choices=tuple(HrDoubt),
                        help="déclare douteuse la FC moyenne connue de l'observation")
    parser.add_argument("--cause", default="", help="la cause déclarée par l'athlète, ses mots")
    values = parser.add_mutually_exclusive_group()
    values.add_argument("--value", type=float)
    values.add_argument("--unavailable", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args()
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if not args.database_url:
        parser.error("DATABASE_URL obligatoire")
    from redline.storage import Database, OperationalStore, StorageError

    if not args.key and not args.date:
        parser.error("--key ou --date obligatoire (cf. `fact-read.py observations`)")
    if args.hr_doubt:
        if args.metric or args.value is not None or args.unavailable:
            parser.error("--fc-douteuse se passe seul, sans --metric ni valeur")
        if not args.cause.strip():
            parser.error("--fc-douteuse exige --cause : la raison donnée par l'athlète")
    elif not args.metric or (args.value is None and not args.unavailable):
        parser.error("--metric et (--value ou --unavailable) obligatoires")
    if args.zone is not None and args.metric != "pain":
        parser.error("--zone ne vaut que pour --metric pain")

    store = OperationalStore(Database(args.database_url, application_name="ledger-complete"))
    # Resolved BEFORE staging: a turn command prepared on a nonexistent key only fails
    # at validation, well after the coach's reply.
    key = resolve_key(store, args.key, args.date, args.kind)
    # Checked NOW, not at the turn boundary: once the reply is written the coach can no
    # longer correct itself and the whole turn falls. Here it reads the error, retries
    # with the right key, and its message goes out intact.
    observation_id = store.observation_id(key)
    if observation_id is None:
        recents = " · ".join(
            f"{row['occurred_on'].isoformat()} {row['kind']} (clé={row['idempotency_key']})"
            for row in store.observation_rows()[:5])
        raise InputError(f"observation inconnue : {key}. Récentes — {recents}")
    if args.hr_doubt:
        payload = {"idempotency_key": key, "reason": args.hr_doubt, "note": args.cause.strip()}
        if turn_id:
            digest = hashlib.sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
            store.stage_command(turn_id, f"hr-doubt:{key}:{digest}", commands.HR_DOUBT,
                                payload)
            print(f"{key} : FC douteuse ({args.hr_doubt}) préparée pour le tour.")
            return 0
        try:
            store.doubt_hr(observation_id, args.hr_doubt, args.cause, actor="ledger-complete")
        except StorageError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"{key} : FC moyenne déclarée douteuse ({args.hr_doubt}).")
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
        print(f"{key} : {args.metric} préparée pour le tour.")
        return 0
    try:
        store.complete_measurement(observation_id, args.metric, args.value, zone=args.zone,
                                   unavailable=args.unavailable, actor="ledger-complete")
    except StorageError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    what = f"{args.metric} {args.zone}" if args.zone else args.metric
    print(f"{key} : {what} complétée.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
