#!/usr/bin/env python3
"""Writes the operational facts other than sessions, without touching the projections.

**`--patch` on `thread` and `deadline`.** Both upserts used to require ALL fields on
every write. Their free-text field can be long, so changing a date meant resending the
whole text. The failure mode is not a typo in the date, which gets reread: it is a
2000-character `--item` retyped from memory and silently truncated, because a full
upsert cannot tell "this field is unchanged" from "this field is now this".

With `--patch`, only the named fields are written; the others are reread from the
existing row. **The queued payload stays COMPLETE**: the merge happens before the
idempotence hash, so `command_key`, `stage_command` and `apply_turn_with_receipts` keep
exactly their contract. Two explicit refusals, never an interpretation: `--patch` on a
missing key refuses (no partial row is created), and without `--patch` a missing
required field refuses by naming it.

Two accepted limits. (1) `--patch` never sets a field back to NULL: going from a value to
"nothing" takes the full form, because an absent option already means "unchanged" and
gets no second meaning. (2) Under `REDLINE_TURN_ID`, the reread happens at queue time
and the write at `apply_turn_with_receipts`: if two commands of the same turn patch the
same key, the second one read the state from BEFORE the first.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from redline.model import (
    commands,
    entrypoint,
    paths,
    session_kinds,
    vault_layout,
)
from redline.model import predictions as prediction_model
from redline.model import vocabulary as voc
from redline.model.errors import ConfigError, InputError

if TYPE_CHECKING:
    from redline.storage import OperationalStore


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
                "champs obligatoires manquants : " + ", ".join(missing)
                + " — sans --patch, une écriture porte la ligne entière")
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
            f"--patch sur une clé inexistante : {key!r} — "
            "créer une ligne demande la forme complète, sans --patch")
    return found[0]


def day(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def week(value: str) -> str:
    if not re.fullmatch(r"20\d{2}-W(?:0[1-9]|[1-4]\d|5[0-3])", value):
        raise argparse.ArgumentTypeError("semaine ISO attendue : 2030-W33")
    return value


def _angle(args, _) -> dict:
    return {"angle": args.angle.strip()}


def _fields(names: tuple[str, ...]):
    return lambda args, _: {name: getattr(args, name) for name in names}


def _daily(args, _) -> dict:
    return {"day": args.date, "sleep": args.sleep, "life_load": args.life_load,
            "desire": args.desire, "note": args.note}


def _deadline(args, existing) -> dict:
    payload = merge(DEADLINE_FIELDS, args, existing)
    payload["kind"] = payload.get("kind") or "general"
    payload["context"] = payload.get("context") or {}
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
            raise InputError(f"--reason {args.reason} ne va pas avec --outcome {args.outcome}"
                             + (f" (attendu : {', '.join(allowed)})" if allowed else ""))
        return {"prediction_key": args.key, "operation": "resolve",
                "outcome": args.outcome == "true" if args.outcome != "void" else None,
                "void": args.outcome == "void", "reason": args.reason}
    if args.reason is not None:
        raise InputError("--reason accompagne --outcome false ou void")
    missing = [name for name in ("domain", "statement", "probability", "due_on")
               if getattr(args, name) is None]
    if missing:
        raise InputError("création de prédiction : options manquantes " + ", ".join(
            "--" + name.replace("_", "-") for name in missing))
    payload = {"prediction_key": args.key, "operation": "create", "domain": args.domain,
               "statement": args.statement, "probability": args.probability,
               "due_on": args.due_on, "direction": args.direction}
    rule = (args.resolves, args.resolves_on, args.success_if)
    if any(part is not None for part in rule):
        if None in rule:
            raise InputError("prédiction rattachée : --resolves, --resolves-on et "
                             "--success-if vont ensemble")
        payload["resolves_on"] = {"kind": args.resolves, "date": args.resolves_on.isoformat(),
                                  "success_if": args.success_if}
        if args.resolves_zone:
            payload["resolves_on"]["zone"] = args.resolves_zone
    elif args.resolves_zone:
        raise InputError("--resolves-zone ne vaut que pour une prédiction rattachée")
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
            f"correction : « {entry} » n'est pas une entrée de {vault_layout.CORRECTIONS}. "
            "Écris d'abord l'entrée (« ## AAAA-MM-JJ — titre »), puis passe son titre exact "
            "à --entry.")
    return {"correction_key": args.correction_key, "summary": args.summary}


def _permit(args, _) -> dict:
    """The coach's choice for a permit easy run, sealed together with its prediction."""
    sunday = dt.date.fromisocalendar(int(args.week[:4]), int(args.week[-2:]), 7)
    statement = (f"SCELLÉ, ne pas révéler à l'athlète avant le bilan {args.week}. Choix du coach "
                 f"pour {args.session_key} du {args.date:%d/%m} : {args.minutes} min. "
                 f"Prédiction : la durée courue par l'athlète tombe à ±10 min de ce choix.")
    return {"iso_week": args.week, "session_key": args.session_key,
            "scheduled_on": args.date, "coach_minutes": args.minutes,
            "prediction_key": f"permis-ef-{args.session_key}", "statement": statement,
            "probability": args.probability, "due_on": sunday}


def _exposure(args, _) -> dict:
    if (args.target_value is None) != (args.target_unit is None):
        raise InputError("--target-value et --target-unit vont ensemble")
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


#: One command, one row: its entity key, its turn command type, how it assembles its
#: payload, and the store method that writes it outside a transaction.
#:
#: ONE table, not two. A second `if/elif` dispatching the direct write would end in an
#: `else` sending any unknown type to `upsert_deadline`; two switches over the same set
#: are bound to drift apart. An unrecognised command refuses instead of being written as
#: a deadline.
class Command(NamedTuple):
    prefix: str
    kind: str
    build: Callable[[Any, Any], dict]
    #: The write outside a turn, called by name so that mypy checks the method and its
    #: signature; None when the command only makes sense within a turn.
    write: Callable[[OperationalStore, dict], object] | None


ACTOR = "fact-write"

COMMANDS: dict[str, Command] = {
    "question": Command("question", commands.QUESTION_REVIEW, _fields((
        "question_id", "answer_id", "status", "note", "follow_up_on")),
        lambda store, row: store.review_question(row, actor=ACTOR)),
    "ask": Command("ask", commands.QUESTION_ASK, _fields(("text", "cells")), None),
    "thread": Command("thread", commands.THREAD_UPSERT,
                      lambda args, existing: merge(THREAD_FIELDS, args, existing),
                      lambda store, row: store.upsert_thread(row, actor=ACTOR)),
    "deadline": Command("deadline", commands.DEADLINE_UPSERT, _deadline,
                        lambda store, row: store.upsert_deadline(row, actor=ACTOR)),
    "daily": Command("daily", commands.DAILY_UPSERT, _daily,
                     lambda store, row: store.put_daily(row, actor=ACTOR)),
    "capability": Command("capability", commands.CAPABILITY_EVALUATE, _fields((
        "capability_number", "slug", "need", "score", "scale", "status",
        "evaluated_on", "next_test", "lock_reason")),
        lambda store, row: store.evaluate_capability(row, actor=ACTOR)),
    # `angle` has NO direct write: outside a turn there is no proactive message to
    # qualify. `main()` refuses before reaching `_direct`.
    "angle": Command("angle", commands.PROACTIVE_ANGLE, _angle, None),
    "gear": Command("gear", commands.GEAR_UPSERT, _fields((
        "gear_id", "name", "role", "retire_at_km", "status")),
        lambda store, row: store.upsert_gear(row, actor=ACTOR)),
    "prediction": Command("prediction", commands.PREDICTION_UPSERT, _prediction,
                          lambda store, row: store.upsert_prediction(row, actor=ACTOR)),
    "correction": Command("correction", commands.CORRECTION_REGISTER, _correction,
        lambda store, row: store.register_correction(row, actor=ACTOR)),
    "exposure": Command("exposure", commands.EXPOSURE_UPSERT, _exposure,
                        lambda store, row: store.upsert_exposure(row, actor=ACTOR)),
    "permit": Command("permit", commands.PERMIT_SEAL, _permit,
                      lambda store, row: store.seal_permit_choice(row, actor=ACTOR)),
}

#: What identifies the targeted entity, when it is not `--key`.
ENTITY_KEY = {
    "question": lambda args, payload: args.question_id,
    "ask": lambda args, payload: hashlib.sha256(args.text.encode()).hexdigest()[:16],
    "daily": lambda args, payload: str(args.date),
    "capability": lambda args, payload: f"{args.capability_number}:{args.evaluated_on}",
    "angle": lambda args, payload: payload["angle"][:60],
    "gear": lambda args, payload: args.gear_id,
    "prediction": lambda args, payload: args.key,
    "correction": lambda args, payload: args.correction_key,
    "exposure": lambda args, payload: args.key,
    "permit": lambda args, payload: f"{args.week}:{args.session_key}",
}


def _payload(args, existing: dict | None = None) -> tuple[str, str, dict]:
    prefix, kind, build, _ = COMMANDS[args.command]
    payload = build(args, existing)
    identity = ENTITY_KEY.get(args.command, lambda args, payload: args.key)(args, payload)
    return f"{prefix}:{identity}", kind, payload


def _direct(store, command: str, payload: dict) -> object:
    write = COMMANDS[command].write
    if write is None:
        raise InputError(f"fact-write : « {command} » ne s'écrit que dans un tour")
    return write(store, payload)


PATCH_HELP = ("--patch : n'écrit que les champs cités, relit les autres depuis la ligne "
              "existante (clé absente = refus ; ne remet aucun champ à NULL)")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    commands = root.add_subparsers(dest="command", required=True)

    daily = commands.add_parser("daily")
    daily.add_argument("--date", type=day, required=True)
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
    capability.add_argument("--evaluated-on", type=day, required=True)
    capability.add_argument("--next-test")
    capability.add_argument("--lock-reason")

    # `thread` and `deadline`: no `required=True` on the fields, otherwise `--patch`
    # would be rejected before being read. The requirement is moved into `merge()`,
    # which NAMES the missing options instead of argparse's terse error.
    question = commands.add_parser("question", help="Examiner une réponse, sans clôture implicite")
    question.add_argument("--question-id", required=True)
    question.add_argument("--answer-id", required=True)
    question.add_argument("--status", choices=("open", "deferred", "answered", "expired"),
                          required=True)
    question.add_argument("--note", required=True)
    question.add_argument("--follow-up-on", type=day)
    ask = commands.add_parser("ask", help="Déclarer la question exacte prévue dans ce tour")
    ask.add_argument("--text", required=True)
    ask.add_argument("--cell", dest="cells", action="append", default=[])

    thread = commands.add_parser("thread", help=PATCH_HELP)
    thread.add_argument("--key", required=True)
    thread.add_argument("--opened-on", type=day, default=UNSET)
    thread.add_argument("--kind", choices=tuple(voc.ThreadKind),
                        default=UNSET)
    thread.add_argument("--body", default=UNSET)
    thread.add_argument("--follow-up-on", type=day, default=UNSET)
    thread.add_argument("--status", choices=tuple(voc.ThreadStatus), default=UNSET)
    thread.add_argument("--patch", action="store_true", help=PATCH_HELP)

    deadline = commands.add_parser("deadline", help=PATCH_HELP)
    deadline.add_argument("--key", required=True)
    deadline.add_argument("--due-on", type=day, default=UNSET)
    deadline.add_argument("--item", default=UNSET)
    deadline.add_argument("--fact-owner", default=UNSET)
    deadline.add_argument("--blocks", default=UNSET)
    deadline.add_argument("--status", choices=("todo", "scheduled", "done"),
                          default=UNSET)
    deadline.add_argument("--completed-on", type=day, default=UNSET)
    deadline.add_argument("--kind", choices=("general", "medical", "availability", "race"),
                          default=UNSET)
    deadline.add_argument("--starts-on", type=day, default=UNSET)
    deadline.add_argument("--ends-on", type=day, default=UNSET)
    deadline.add_argument("--context", type=json.loads, default=UNSET,
                          help="objet JSON réservé aux détails du scénario")
    deadline.add_argument(
        "--athlete-label", default=UNSET,
        help="libellé court (3-80 car.) : l'échéance est une action de l'athlète, montrée "
             "dans son carnet ; une chaîne vide la rend au coach seul")
    deadline.add_argument("--patch", action="store_true", help=PATCH_HELP)

    # The angle has a FIELD, so it does not belong in prose: without a place to record
    # it, the reasoning about served angles ends up in the message sent to the athlete.
    angle = commands.add_parser(
        "angle", help="qualifie le message proactif de CE tour (n'a de sens qu'en tour)")
    angle.add_argument("angle", help="deux ou trois mots, ex. « titre Strava, récit libre »")
    permit = commands.add_parser(
        "permit", help="scelle le choix du coach pour une EF du permis, AVANT que l'athlète décide")
    permit.add_argument("--week", type=week, required=True)
    permit.add_argument("--session-key", required=True)
    permit.add_argument("--date", type=day, required=True)
    permit.add_argument("--minutes", type=int, required=True)
    permit.add_argument("--probability", type=float, default=0.6)
    gear = commands.add_parser("gear")
    gear.add_argument("--gear-id", required=True)
    gear.add_argument("--name", required=True)
    gear.add_argument("--role", required=True)
    gear.add_argument("--retire-at-km", type=float, required=True)
    gear.add_argument("--status", choices=("active", "retired"), default="active")

    prediction = commands.add_parser("prediction")
    prediction.add_argument("--key", required=True)
    prediction.add_argument("--domain")
    prediction.add_argument("--statement")
    prediction.add_argument("--probability", type=float)
    prediction.add_argument("--due-on", type=day)
    prediction.add_argument("--direction", choices=("increase", "decrease", "neutral"),
                            default="neutral")
    prediction.add_argument("--outcome", choices=("true", "false", "void"))
    prediction.add_argument(
        "--reason", choices=(*prediction_model.REFUTED_REASONS, *prediction_model.VOID_REASONS),
        help="pourquoi : optimistic/cautious pour une réfutée (moins bien ou mieux que "
             "prédit) ; instrument/exposure/scope/superseded pour une void")
    prediction.add_argument(
        "--resolves", choices=(session_kinds.WAKE_TRACE,),
        help="observation qui solde seule la prédiction à son écriture (void si la date "
             "passe sans elle)")
    prediction.add_argument("--resolves-on", type=day, metavar="AAAA-MM-JJ")
    prediction.add_argument(
        "--resolves-zone", metavar="ZONE",
        help="la zone surveillée dont la trace tranche ; obligatoire s'il y en a plusieurs")
    prediction.add_argument(
        "--success-if", metavar="CRITÈRE",
        help="seuil sur la valeur observée, ex. '<=1' (trace au réveil : 0 aucune, "
             "0.5 très légère, 1 légère, 2 nette)")
    prediction.add_argument("--challenge")
    prediction.add_argument(
        "--rectify", metavar="MOTIF",
        help="repasse en void une prédiction soldée dont la condition n'a pas été tenue "
             "ou dont l'observation manque")

    correction = commands.add_parser("correction")
    correction.add_argument("--correction-key", required=True)
    correction.add_argument("--summary", required=True)
    correction.add_argument(
        "--entry", required=True,
        help="titre exact de l'entrée de coaching/corrections.md, après « ## »")
    exposure = commands.add_parser("exposure")
    exposure.add_argument("--key", required=True)
    exposure.add_argument("--observation-key", required=True)
    exposure.add_argument(
        "--exposure-type",
        choices=tuple(voc.Exposure),
        required=True)
    exposure.add_argument(
        "--origin", choices=("prescribed", "voluntary", "spontaneous"), required=True)
    exposure.add_argument(
        "--status", choices=("candidate", "confirmed", "dismissed"), required=True)
    exposure.add_argument("--duration-min", type=int)
    exposure.add_argument("--target-value", type=float)
    exposure.add_argument("--target-unit")
    exposure.add_argument(
        "--tolerance", choices=("unknown", "easy", "strained", "protective"),
        default="unknown")
    exposure.add_argument("--note", default="")
    return root


def main() -> int:
    args = parser().parse_args()
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if not args.database_url:
        raise ConfigError("DATABASE_URL obligatoire : les projections sont en lecture seule")
    from redline.storage import Database, OperationalStore, StorageError

    store = OperationalStore(Database(args.database_url, application_name="fact-write"))
    # The merge precedes the hash: the queued payload stays complete, so `command_key`
    # and `apply_turn_with_receipts` keep the contract they had before `--patch`.
    existing = _existing(store, args.command, args.key) if getattr(
        args, "patch", False) else None
    if args.command in {"angle", "ask"} and not turn_id:
        raise InputError(
            "Cette commande qualifie le message du tour en cours : hors d'un tour "
            "(REDLINE_TURN_ID absent) il n'y a aucun message à qualifier.")
    entity_key, kind, payload = _payload(args, existing)
    canonical = json.dumps(payload, default=lambda value: value.isoformat(),
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    command_key = f"{entity_key}:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"
    if turn_id and args.command in {"ask", "question"}:
        command_key = f"{turn_id}:{command_key}"
    # A store refusal exits as a named error, never as a success: a write ignored because
    # of a conflict must not be announced as recorded.
    try:
        if turn_id:
            store.stage_command(turn_id, command_key, kind, payload)
            print(f"{entity_key} préparé pour le tour.")
            return 0
        written = _direct(store, args.command, payload)
    except StorageError as exc:
        raise StorageError(f"fact-write : {entity_key} refusé : {exc}") from exc
    # False = the same row already existed; nothing was written.
    print(f"{entity_key} déjà enregistré, rien d'écrit." if written is False
          else f"{entity_key} enregistré.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
