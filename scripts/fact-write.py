#!/usr/bin/env python3
"""Écrit les faits opérationnels hors séances sans toucher aux projections.

**`--patch` sur `thread` et `deadline` (2026-08-15).** Ces deux upserts exigeaient
TOUS les champs à chaque écriture. Or leur champ de texte libre est long — l'échéance
`legacy-deadline:19` faisait 1400 caractères — donc changer une date obligeait à
renvoyer le texte entier. Le mode d'échec n'est pas la faute de frappe dans la date,
qu'on relit : c'est le `--item` de 2000 caractères réécrit de mémoire, tronqué sans
que rien ne le signale, parce qu'un upsert complet ne peut pas distinguer « ce champ
est inchangé » de « ce champ vaut maintenant ceci ». Le 2026-08-15, recaler cette
échéance a dû passer par un script de relecture ad hoc plutôt que par ce CLI : le
signe que l'outil manquait.

Avec `--patch`, seuls les champs cités sont écrits ; les autres sont relus depuis la
ligne existante. **Le payload mis en file reste COMPLET** : la fusion a lieu avant le
hachage d'idempotence, donc `command_key`, `stage_command` et `apply_turn_with_receipts` gardent
exactement leur contrat. Deux refus explicites, jamais d'interprétation —
`--patch` sur une clé absente refuse (on ne crée pas une ligne à trous), et sans
`--patch` un champ obligatoire manquant refuse en le nommant.

⚠ **Deux limites assumées.** (1) `--patch` ne remet aucun champ à NULL : passer d'une
valeur à « rien » demande la forme complète, parce qu'une option absente veut déjà dire
« inchangé » et qu'on ne va pas lui donner un second sens. (2) Sous `REDLINE_TURN_ID`,
la relecture a lieu à la mise en file et l'écriture à `apply_turn_with_receipts` : si deux commandes
du même tour patchent la même clé, la seconde a lu l'état d'AVANT la première.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model import (  # noqa: E402
    commands,
    entrypoint,
    paths,
    session_kinds,
    vault_layout,
)
from scripts.model import predictions as prediction_model  # noqa: E402
from scripts.model.errors import ConfigError, InputError  # noqa: E402

if TYPE_CHECKING:
    from scripts.storage import OperationalStore


class _Unset:
    """« Option absente », qu'il faut distinguer de « option à None »."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()

# (clé de payload, nom argparse, obligatoire à la création)
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
    """Le payload complet : ce qui est fourni, posé sur la ligne existante.

    `existing is None` veut dire création — tout champ obligatoire manquant est un
    refus nommé, ce que faisait `required=True` avant que `--patch` ne l'interdise
    au niveau d'argparse.
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
    """La ligne visée par `--patch`, ou un refus. On ne crée pas à trous."""
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
        raise argparse.ArgumentTypeError("semaine ISO attendue : 2026-W33")
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
    """Une correction n'entre en base qu'une fois écrite dans `corrections.md`.

    Audit du 2026-10-09 : douze corrections n'existaient que dans PostgreSQL. Ni le
    contrôle des porteurs ni le registre de rejeu (`correction_replay.py`), qui lisent
    le journal, ne les voyaient : l'erreur était annoncée à l'athlète, jamais apprise.
    """
    from scripts.model import correction_replay

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
    """Le choix du coach pour une EF du permis, scellé avec sa prédiction (2026-09-27)."""
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


#: Une commande, une ligne : sa clé d'entité, son type de commande de tour, comment
#: elle assemble sa charge, et la méthode du magasin qui l'écrit hors transaction.
#:
#: ⭐ UNE table, et non deux. Le type de commande était décidé ici et l'écriture directe
#: dans un second `if/elif` ailleurs, dont la dernière branche était un `else` qui
#: envoyait TOUT type inconnu vers `upsert_deadline`. La retombée muette avait déjà été
#: retirée du premier aiguillage (« une commande non reconnue refuse au lieu d'être
#: écrite comme une échéance ») et laissée intacte dans le second : deux aiguillages
#: pour le même ensemble, c'est la garantie qu'un jour l'un des deux se corrige seul.
class Command(NamedTuple):
    prefix: str
    kind: str
    build: Callable[[Any, Any], dict]
    #: L'écriture hors tour, appelée par son nom pour que mypy vérifie la méthode et sa
    #: signature ; None quand la commande n'a de sens que dans un tour.
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
    # `angle` n'a PAS d'écriture directe : hors d'un tour, il n'y a pas de message
    # proactif à qualifier. `main()` refuse avant d'arriver à `_direct`.
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

#: Ce qui identifie l'entité visée, quand ce n'est pas `--key`.
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
    daily.add_argument("--sleep", choices=("bonne", "moyenne", "mauvaise"))
    daily.add_argument("--life-load", choices=("legere", "normale", "elevee"))
    daily.add_argument("--desire", choices=("forte", "normale", "basse"))
    daily.add_argument("--note", default="")

    capability = commands.add_parser("capability")
    capability.add_argument("--capability-number", type=int, required=True)
    capability.add_argument("--slug", required=True)
    capability.add_argument("--need", required=True)
    capability.add_argument("--score", type=int, required=True)
    capability.add_argument("--scale", type=int, required=True)
    capability.add_argument("--status", choices=("rouge", "orange", "vert"), required=True)
    capability.add_argument("--evaluated-on", type=day, required=True)
    capability.add_argument("--next-test")
    capability.add_argument("--lock-reason")

    # `thread` et `deadline` : plus de `required=True` sur les champs, sinon `--patch`
    # serait rejeté avant d'être lu. L'obligation n'est pas perdue, elle est déplacée
    # dans `merge()`, qui NOMME les options manquantes au lieu du laconique argparse.
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
    thread.add_argument("--kind", choices=("promesse", "ressenti", "suspens"),
                        default=UNSET)
    thread.add_argument("--body", default=UNSET)
    thread.add_argument("--follow-up-on", type=day, default=UNSET)
    thread.add_argument("--status", choices=("ouvert", "clos"), default=UNSET)
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

    # ⭐ L'angle a un CHAMP, donc il n'a rien à faire dans la prose (2026-08-21). La
    # colonne `proactive_messages.angle` existait, `fact-read.py proactive` l'affichait,
    # le déclencheur du débrief exigeait « un angle que tu n'as pas déjà servi » — et
    # rien ne l'écrivait jamais : `vault.py` posait `angle: None` en dur. Le modèle
    # devait donc reconstituer les angles servis à partir des résumés bruts, et n'ayant
    # aucun endroit où déposer ce raisonnement, il l'a déposé dans le message que l'athlète
    # a reçu. Un garde-fou qui n'ouvre pas de chemin correct fabrique son contournement.
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
        choices=("cadence", "descente", "lignes", "nutrition", "equipement", "autre"),
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
    from scripts.storage import Database, OperationalStore, StorageError

    store = OperationalStore(Database(args.database_url, application_name="fact-write"))
    # La fusion précède le hachage : le payload mis en file reste complet, donc
    # `command_key` et `apply_turn_with_receipts` gardent le contrat qu'ils avaient avant `--patch`.
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
    # Un refus du magasin sort en erreur nommée, jamais en succès : le 2026-10-13,
    # une évaluation de capacité ignorée par un conflit était annoncée « enregistré ».
    try:
        if turn_id:
            store.stage_command(turn_id, command_key, kind, payload)
            print(f"{entity_key} préparé pour le tour.")
            return 0
        written = _direct(store, args.command, payload)
    except StorageError as exc:
        raise StorageError(f"fact-write : {entity_key} refusé : {exc}") from exc
    # False = la même ligne existait déjà ; rien n'a été écrit.
    print(f"{entity_key} déjà enregistré, rien d'écrit." if written is False
          else f"{entity_key} enregistré.")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
