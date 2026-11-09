#!/usr/bin/env python3
"""Writes the operational facts other than sessions, without touching the projections.

**`--patch` on `thread` and `deadline`.** Without it, both upserts require ALL fields on
every write. Their free-text field can be long, so changing a date means resending the
whole text. The failure mode is not a typo in the date, which gets reread: it is a
2000-character `--item` retyped from memory and silently truncated, because a full
upsert cannot tell "this field is unchanged" from "this field is now this".

With `--patch`, only the named fields are written; the others are reread from the
existing row. **The queued payload stays COMPLETE**: the merge happens before the
idempotence hash, so `command_key`, `stage_command` and `apply_turn_with_receipts` keep
exactly their contract. Two explicit refusals, never an interpretation: `--patch` on a
missing key refuses (no partial row is created), and without `--patch` a missing
required field refuses by naming it.

**`--context` under `--patch` merges key by key.** The context is the one field made of
fields: replacing it whole would erase every key the caller did not retype (a race's
start time, its taper, its aid stations). So the named keys are written over the stored
ones, and a key given as `null` is removed.

Two accepted limits. (1) `--patch` never sets a field back to NULL: going from a value to
"nothing" takes the full form, because an absent option already means "unchanged" and
gets no second meaning. (2) Under `REDLINE_TURN_ID`, the reread happens at queue time
and the write at `apply_turn_with_receipts`: if two commands of the same turn patch the
same key, the second one read the state from BEFORE the first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from typing import Any, NamedTuple

from redline.cli import _args
from redline.coach.fr import fact_write as wording
from redline.model import (
    agent_input,
    commands,
    entrypoint,
    iso_week,
    paths,
    session_kinds,
    vault_layout,
)
from redline.model import predictions as prediction_model
from redline.model import race as race_model
from redline.model import vocabulary as voc
from redline.model.errors import InputError


class _Unset:
    """An absent option, which must be told apart from an option set to None."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()

# (payload key, argparse name, required at creation)
THREAD_FIELDS = (
    ("thread_key", "key", True),
    ("opened_on", "opened_on", True),
    ("kind", "kind", True),
    ("body", "body", True),
    ("follow_up_on", "follow_up_on", False),
    ("status", "status", True),
)
DEADLINE_FIELDS = (
    ("deadline_key", "key", True),
    ("due_on", "due_on", True),
    ("item", "item", True),
    ("fact_owner", "fact_owner", True),
    ("blocks", "blocks", False),
    ("status", "status", True),
    ("completed_on", "completed_on", False),
    ("kind", "kind", False),
    ("starts_on", "starts_on", False),
    ("ends_on", "ends_on", False),
    ("context", "context", False),
    ("athlete_label", "athlete_label", False),
)


def merge(fields: tuple, args: argparse.Namespace, existing: dict | None) -> dict:
    """The complete payload: what is supplied, laid over the existing row.

    `existing is None` means creation: any missing required field is a named refusal,
    which is what `required=True` did before `--patch` made it impossible at the
    argparse level.
    """
    if existing is None:
        missing = ["--" + name.replace("_", "-")
                   for _, name, required in fields
                   if required and getattr(args, name, UNSET) is UNSET]
        if missing:
            raise InputError(
                "required fields missing: " + ", ".join(missing)
                + " -- without --patch, a write carries the whole row")
    payload = {}
    for key, name, _ in fields:
        value = getattr(args, name, UNSET)
        payload[key] = (existing.get(key) if existing is not None else None) \
            if value is UNSET else value
    return payload


def _existing(store: Any, command: str, key: str) -> dict:
    """The row targeted by `--patch`, or a refusal. No partial row is created."""
    rows = (store.thread_rows() if command == "thread" else store.deadline_rows())
    field = "thread_key" if command == "thread" else "deadline_key"
    found = [row for row in rows if row[field] == key]
    if not found:
        raise InputError(
            f"--patch on a key that does not exist: {key!r} -- creating a row takes the full"
            " form, without --patch")
    return found[0]


def _angle(args, _) -> dict:
    return {"angle": args.angle.strip()}


def _fields(names: tuple[str, ...]):
    return lambda args, _: {name: getattr(args, name) for name in names}


def _daily(args, _) -> dict:
    return {"day": args.date, "sleep": args.sleep, "life_load": args.life_load,
            "desire": args.desire, "note": args.note}


def _deadline(args, existing) -> dict:
    payload = merge(DEADLINE_FIELDS, args, existing)
    payload["kind"] = payload.get("kind") or voc.DeadlineKind.GENERAL
    if getattr(args, "context", UNSET) is not UNSET and not isinstance(args.context, dict):
        raise InputError("--context: a JSON object")
    if existing is not None and getattr(args, "context", UNSET) is not UNSET:
        merged = {**(existing.get("context") or {}), **args.context}
        payload["context"] = {key: value for key, value in merged.items() if value is not None}
    payload["context"] = payload.get("context") or {}
    if payload["kind"] == voc.DeadlineKind.RACE:
        race_model.CONTEXT_SCHEMA.validate(payload["context"], "context")
    payload["athlete_label"] = (payload.get("athlete_label") or "").strip() or None
    return payload


def _prediction(args, _) -> dict:
    if args.challenge is not None:
        return {"prediction_key": args.key, "operation": "challenge",
                "challenge": args.challenge}
    if args.rectify is not None:
        return {"prediction_key": args.key, "operation": "rectify", "reason": args.rectify}
    if args.outcome is not None:
        allowed = {"false": prediction_model.REFUTED_REASONS,
                   "void": prediction_model.VOID_REASONS}.get(args.outcome, ())
        if args.reason is not None and args.reason not in allowed:
            raise InputError(f"--reason {args.reason} does not go with --outcome {args.outcome}"
                             + (f" (expected: {", ".join(allowed)})" if allowed else ""))
        return {"prediction_key": args.key, "operation": "resolve",
                "outcome": args.outcome == "true" if args.outcome != "void" else None,
                "void": args.outcome == "void", "reason": args.reason}
    if args.reason is not None:
        raise InputError("--reason goes with --outcome false or void")
    missing = [name for name in ("domain", "statement", "probability", "due_on")
               if getattr(args, name) is None]
    if missing:
        raise InputError("creating a prediction: missing options " + ", ".join(
            "--" + name.replace("_", "-") for name in missing))
    payload = {"prediction_key": args.key, "operation": "create", "domain": args.domain,
               "statement": args.statement, "probability": args.probability,
               "due_on": args.due_on, "direction": args.direction}
    rule = (args.resolves, args.resolves_on, args.success_if)
    if any(part is not None for part in rule):
        if None in rule:
            raise InputError("attached prediction: --resolves, --resolves-on and --success-if "
                             "go together")
        payload["resolves_on"] = {"kind": args.resolves, "date": args.resolves_on.isoformat(),
                                  "success_if": args.success_if}
        if args.resolves_zone:
            payload["resolves_on"]["zone"] = args.resolves_zone
    elif args.resolves_zone:
        raise InputError("--resolves-zone only applies to an attached prediction")
    return payload


def _correction(args, _) -> dict:
    """A correction enters the database only once written in `corrections.md`.

    The owner check and the replay registry (`correction_replay.py`) read the journal, so
    a correction that exists only in PostgreSQL is invisible to both.
    """
    from redline.model import correction_replay

    journal = paths.vault_root() / vault_layout.CORRECTIONS
    known = {entry.key for entry in correction_replay.entries(journal.read_text(encoding="utf-8"))}
    entry = args.entry.strip()
    if entry not in known:
        raise InputError(
            f"correction: {entry!r} is not an entry of {vault_layout.CORRECTIONS}. Write the "
            "entry first (`## YYYY-MM-DD — title`), then pass its exact title to --entry.")
    return {"correction_key": args.correction_key, "summary": args.summary}


def _permit(args, _) -> dict:
    """The coach's choice for a permit easy run, sealed together with its prediction."""
    sunday = iso_week.sunday(args.week)
    statement = (wording.SEALED_CHOICE.format(week=args.week, session_key=args.session_key,
                                              date=args.date, minutes=args.minutes))
    return {"iso_week": args.week, "session_key": args.session_key,
            "scheduled_on": args.date, "coach_minutes": args.minutes,
            "prediction_key": f"easy-permit-{args.session_key}", "statement": statement,
            "probability": args.probability, "due_on": sunday}


def _exposure(args, _) -> dict:
    if (args.target_value is None) != (args.target_unit is None):
        raise InputError("--target-value and --target-unit go together")
    return {
        "exposure_key": args.key,
        "observation_key": args.observation_key,
        "exposure_type": args.exposure_type,
        "origin": args.origin,
        "status": args.status,
        "duration_min": args.duration_min,
        "target_value": args.target_value,
        "target_unit": args.target_unit,
        "tolerance": args.tolerance,
        "note": args.note,
    }


#: One command, one row: its entity key, its turn command type and how it assembles its
#: payload. Within a turn and outside one, the store writes it through the same handler.
class Command(NamedTuple):
    kind: str
    build: Callable[[Any, Any], dict]
    #: What identifies the targeted entity; `--key` when None.
    entity: Callable[[Any, dict], str] | None = None


ACTOR = "fact-write"

#: The commands that qualify the message of the current turn: outside a turn there is
#: no message to qualify.
TURN_ONLY = frozenset({"angle", "ask"})

COMMANDS: dict[str, Command] = {
    "question": Command(commands.QUESTION_REVIEW, _fields((
        "question_id", "answer_id", "status", "note", "follow_up_on")),
        lambda args, payload: args.question_id),
    "ask": Command(commands.QUESTION_ASK, _fields(("text", "cells")),
                   lambda args, payload: hashlib.sha256(args.text.encode()).hexdigest()[:16]),
    "thread": Command(commands.THREAD_UPSERT,
                      lambda args, existing: merge(THREAD_FIELDS, args, existing)),
    "deadline": Command(commands.DEADLINE_UPSERT, _deadline),
    "daily": Command(commands.DAILY_UPSERT, _daily, lambda args, payload: str(args.date)),
    "capability": Command(commands.CAPABILITY_EVALUATE, _fields((
        "capability_number", "slug", "need", "score", "scale", "status",
        "evaluated_on", "next_test", "lock_reason")),
        lambda args, payload: f"{args.capability_number}:{args.evaluated_on}"),
    "angle": Command(commands.PROACTIVE_ANGLE, _angle, lambda args, payload: payload["angle"][:60]),
    "gear": Command(commands.GEAR_UPSERT, _fields((
        "gear_id", "name", "role", "retire_at_km", "status")),
        lambda args, payload: args.gear_id),
    "prediction": Command(commands.PREDICTION_UPSERT, _prediction),
    "correction": Command(commands.CORRECTION_REGISTER, _correction,
                          lambda args, payload: args.correction_key),
    "exposure": Command(commands.EXPOSURE_UPSERT, _exposure),
    "permit": Command(commands.PERMIT_SEAL, _permit,
                      lambda args, payload: f"{args.week}:{args.session_key}"),
}


def _payload(args, existing: dict | None = None) -> tuple[str, str, dict]:
    kind, build, entity = COMMANDS[args.command]
    payload = build(args, existing)
    identity = entity(args, payload) if entity else args.key
    return f"{args.command}:{identity}", kind, payload


PATCH_HELP = ("--patch: writes only the cited fields and rereads the others from the existing"
               " row (missing key = refusal; resets no field to NULL); --context then merges"
               " key by key, a key set to null is removed")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    daily = commands.add_parser("daily")
    daily.add_argument("--date", type=_args.day, required=True)
    daily.add_argument("--sleep", choices=tuple(voc.Sleep))
    daily.add_argument("--life-load", choices=tuple(voc.LifeLoad))
    daily.add_argument("--desire", choices=tuple(voc.Desire))
    daily.add_argument("--note", default="")

    capability = commands.add_parser("capability")
    capability.add_argument("--capability-number", type=int, required=True)
    capability.add_argument("--slug", required=True)
    capability.add_argument("--need", required=True)
    capability.add_argument("--score", type=int, required=True)
    capability.add_argument("--scale", type=int, required=True)
    capability.add_argument("--status", choices=tuple(voc.CapabilityStatus), required=True)
    capability.add_argument("--evaluated-on", type=_args.day, required=True)
    capability.add_argument("--next-test")
    capability.add_argument("--lock-reason")

    # `thread` and `deadline`: no `required=True` on the fields, otherwise `--patch`
    # would be rejected before being read. The requirement is moved into `merge()`,
    # which NAMES the missing options instead of argparse's terse error.
    question = commands.add_parser("question",
                                   help="review an answer, without closing it implicitly")
    question.add_argument("--question-id", required=True)
    question.add_argument("--answer-id", required=True)
    question.add_argument("--status", choices=tuple(voc.QuestionStatus),
                          required=True)
    question.add_argument("--note", required=True)
    question.add_argument("--follow-up-on", type=_args.day)
    ask = commands.add_parser("ask", help="declare the exact question planned for this turn")
    ask.add_argument("--text", required=True)
    ask.add_argument("--cell", dest="cells", action="append", default=[])

    thread = commands.add_parser("thread", help=PATCH_HELP)
    thread.add_argument("--key", required=True)
    thread.add_argument("--opened-on", type=_args.day, default=UNSET)
    thread.add_argument("--kind", choices=tuple(voc.ThreadKind),
                        default=UNSET)
    thread.add_argument("--body", default=UNSET)
    thread.add_argument("--follow-up-on", type=_args.day, default=UNSET)
    thread.add_argument("--status", choices=tuple(voc.ThreadStatus), default=UNSET)
    thread.add_argument("--patch", action="store_true", help=PATCH_HELP)

    deadline = commands.add_parser("deadline", help=PATCH_HELP)
    deadline.add_argument("--key", required=True)
    deadline.add_argument("--due-on", type=_args.day, default=UNSET)
    deadline.add_argument("--item", default=UNSET)
    deadline.add_argument("--fact-owner", default=UNSET)
    deadline.add_argument("--blocks", default=UNSET)
    deadline.add_argument("--status", choices=tuple(voc.DeadlineStatus),
                          default=UNSET)
    deadline.add_argument("--completed-on", type=_args.day, default=UNSET)
    deadline.add_argument("--kind", choices=tuple(voc.DeadlineKind),
                          default=UNSET)
    deadline.add_argument("--starts-on", type=_args.day, default=UNSET)
    deadline.add_argument("--ends-on", type=_args.day, default=UNSET)
    deadline.add_argument("--context", type=json.loads, default=UNSET,
                          help="JSON object of the deadline's details; a race may set its taper "
                               "(taper_weeks, taper_cut_min_pct, taper_cut_max_pct) and its "
                               "plan (route_gpx, aid_stations, stop_minutes, carbs_g_h, "
                               "pace_s_per_km)")
    deadline.add_argument(
        "--athlete-label", default=UNSET,
        help="short label (3-80 chars): the deadline is an action of the athlete, shown in "
             "their notebook; an empty string keeps it for the coach alone")
    deadline.add_argument("--patch", action="store_true", help=PATCH_HELP)

    # The angle has a FIELD, so it does not belong in prose: without a place to record
    # it, the reasoning about served angles ends up in the message sent to the athlete.
    angle = commands.add_parser(
        "angle", help="qualifies THIS turn's proactive message (only makes sense within a turn)")
    angle.add_argument("angle", help="two or three words, e.g. Strava title, free story")
    permit = commands.add_parser(
        "permit", help="seals the coach's choice for an easy run of the permit, BEFORE the athlete "
                       "decides")
    permit.add_argument("--week", type=_args.week, required=True)
    permit.add_argument("--session-key", required=True)
    permit.add_argument("--date", type=_args.day, required=True)
    permit.add_argument("--minutes", type=int, required=True)
    permit.add_argument("--probability", type=float, default=0.6)
    gear = commands.add_parser("gear")
    gear.add_argument("--gear-id", required=True)
    gear.add_argument("--name", required=True)
    gear.add_argument("--role", required=True)
    gear.add_argument("--retire-at-km", type=float, required=True)
    gear.add_argument("--status", choices=tuple(voc.GearStatus), default=voc.GearStatus.ACTIVE)

    prediction = commands.add_parser("prediction")
    prediction.add_argument("--key", required=True)
    prediction.add_argument("--domain")
    prediction.add_argument("--statement")
    prediction.add_argument("--probability", type=float)
    prediction.add_argument("--due-on", type=_args.day)
    prediction.add_argument("--direction", choices=tuple(voc.Direction),
                            default="neutral")
    prediction.add_argument("--outcome", choices=("true", "false", "void"))
    prediction.add_argument(
        "--reason", choices=(*prediction_model.REFUTED_REASONS, *prediction_model.VOID_REASONS),
        help="why: optimistic/cautious for a refuted one (worse or better than predicted); "
             "instrument/exposure/scope/superseded for a void one")
    prediction.add_argument(
        "--resolves", choices=(session_kinds.WAKE_TRACE,),
        help="observation that alone settles the prediction when it is written (void if the "
             "date passes without it)")
    prediction.add_argument("--resolves-on", type=_args.day, metavar="AAAA-MM-JJ")
    prediction.add_argument(
        "--resolves-zone", metavar="ZONE",
        help="the monitored zone whose trace decides; required when there are several")
    prediction.add_argument(
        "--success-if", metavar="CRITERION",
        help="threshold on the observed value, e.g. '<=1' (wake trace: 0 none, 0.5 very "
             "light, 1 light, 2 clear)")
    prediction.add_argument("--challenge")
    prediction.add_argument(
        "--rectify", metavar="MOTIF",
        help="turns back to void a settled prediction whose condition was not met or whose "
             "observation is missing")

    correction = commands.add_parser("correction")
    correction.add_argument("--correction-key", required=True)
    correction.add_argument("--summary", required=True)
    correction.add_argument(
        "--entry", required=True,
        help="exact title of the coaching/corrections.md entry, after `##`")
    exposure = commands.add_parser("exposure")
    exposure.add_argument("--key", required=True)
    exposure.add_argument("--observation-key", required=True)
    exposure.add_argument(
        "--exposure-type",
        choices=tuple(voc.Exposure),
        required=True)
    exposure.add_argument(
        "--origin", choices=tuple(voc.ExposureOrigin), required=True)
    exposure.add_argument(
        "--status", choices=tuple(voc.ExposureStatus), required=True)
    exposure.add_argument("--duration-min", type=int)
    exposure.add_argument("--target-value", type=float)
    exposure.add_argument("--target-unit")
    exposure.add_argument(
        "--tolerance", choices=tuple(voc.Tolerance),
        default=voc.Tolerance.UNKNOWN)
    exposure.add_argument("--note", default="")
    return root


def main() -> int:
    args = parser().parse_args()
    turn_id = agent_input.current_turn()
    from redline.storage import StorageError, open_store

    store = open_store(ACTOR)
    # The merge precedes the hash: the queued payload stays complete, so `command_key`
    # and `apply_turn_with_receipts` keep the contract they had before `--patch`.
    existing = _existing(store, args.command, args.key) if getattr(
        args, "patch", False) else None
    if args.command in TURN_ONLY and not turn_id:
        raise InputError(
            "This command qualifies the message of the current turn: outside a turn "
            f"({agent_input.TURN_VARIABLE} missing) there is no message to qualify.")
    entity_key, kind, payload = _payload(args, existing)
    command_key = f"{entity_key}:{commands.digest(payload)}"
    if turn_id and args.command in {"ask", "question"}:
        command_key = f"{turn_id}:{command_key}"
    # A store refusal exits as a named error, never as a success: a write ignored because
    # of a conflict must not be announced as recorded.
    try:
        submitted = store.submit(kind, payload, command_key=command_key, turn_id=turn_id,
                                 actor=ACTOR)
    except StorageError as exc:
        raise StorageError(f"fact-write: {entity_key} refused: {exc}") from exc
    if submitted.staged:
        print(wording.STAGED.format(entity_key=entity_key))
    # False = the same row already existed; nothing was written.
    elif submitted.result is False:
        print(wording.ALREADY_RECORDED.format(entity_key=entity_key))
    else:
        print(wording.RECORDED.format(entity_key=entity_key))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
