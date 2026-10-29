#!/usr/bin/env python3
"""Create/revise/show/check the canonical weekly training plan.

Exit codes of `check` and `show`; the distinction carries everything else:

- ``0`` prescribable;
- ``2`` valid but AWAITING AN ANSWER FROM THE ATHLETE: a dated check is due. The
  program is intact, `show` still prints it on stdout, and the right move is to ASK the
  check's question (it is on stderr), not to repair anything;
- ``3`` valid but TO BE REVIEWED: an activity not attached to a planned session appeared
  since the plan was computed. Intact and printed as well; the right move is to arbitrate
  that unplanned load, not to copy the completed sessions;
- ``1`` invalid: missing or unreadable. Only there is nothing readable and nothing printed.

Confusing 2 with 1 suppresses the morning message on check days. Confusing 3 with 1 is
worse: the CLI reports "invalid" with an EMPTY stdout while the service reports "stale,
you may answer", so the coach concludes the program is broken although it is in its
prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from redline.model import (
    agent_input,
    commands,
    entrypoint,
)
from redline.model.errors import ConfigError
from redline.model.notes import NoteError
from redline.model.schema import SchemaError
from redline.model.weekly_plan import (
    active,
    as_json,
    change_evidence_errors,
    current_week,
    gate_reasons,
    intention_errors,
    now_stamp,
    revision_readback,
    typed_target_errors,
    validate,
    written_text_errors,
)
from redline.storage import StorageError


def _distance_notes(store, revision: dict) -> str:
    """The gap to the rule n8 window for each upcoming session (informative)."""
    import datetime as dt

    from redline.analysis import ledger
    from redline.model import rulebook
    days = rulebook.current().parameters.transition_lookback_days
    try:
        rows = ledger.parse_ledger(store)
    except Exception as exc:
        return f"Écart aux {days} jours : lecture du registre impossible ({exc.__class__.__name__})."
    # The revision timestamp is the service's (`now_stamp`): it is the day "upcoming" is
    # judged from, not the clock of the machine rereading.
    written_on = dt.date.fromisoformat(str(revision["computed_at"])[:10])
    notes = ledger.transition_notes(rows, revision["sessions"], written_on)
    if not notes:
        return ""
    return "\n".join([f"Écart aux {days} jours (règle n8, informatif, jamais bloquant) :",
                      *(f"  - {note}" for note in notes)])


EXIT_OK = 0
EXIT_INVALID = 1
EXIT_AWAITING_GATE = 2
EXIT_STALE = 3


def _database_store():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise ConfigError("DATABASE_URL obligatoire : le journal ne contient qu'une projection")
    from redline.storage import Database, OperationalStore
    return OperationalStore(Database(url, application_name="week-plan"))


def _database_command(store, command: str, week: str, optional: bool,
                      input_path: str | None = None) -> int:
    if command in {"revise", "preview"}:
        # A coach turn hands its JSON over via `--input`, never `<`: see
        # `src/redline/model/agent_input.py`. stdin remains the operator-launch path.
        revision = agent_input.read_json(input_path)
        existing = store.load_plan(week)
        expected = 1 if existing is None else len(existing["revisions"]) + 1
        if revision.get("revision") != expected:
            raise SchemaError([f"revision : attendu {expected}, reçu {revision.get('revision')!r}"])
        current = dict(revision)
        # The timestamp belongs to the service, not the model. A model that invents the
        # time of day also invents the day the athlete is talking about, and
        # `semantic_errors` compares a dated check's due date to this field, so a wrong
        # time would disarm a guardrail. The only reliable clock in the chain is the one
        # that writes.
        current["computed_at"] = now_stamp()
        # The incoming text is judged before the plan containing it: the revision is fed
        # back to the coach on every prescriptive turn, and title/note go to the athlete's
        # phone. Judged here and not in `validate` because history is append-only: the
        # same rule applied to the whole plan would refuse today's revision because of
        # yesterday's text.
        previous = existing["revisions"][-1] if existing else None
        mistakes = [
            *intention_errors(current, f"{week} révision {current['revision']}"),
            *written_text_errors(current, f"{week} révision {current['revision']}"),
            *change_evidence_errors(previous, current),
            *typed_target_errors(current, f"{week} révision {current['revision']}"),
        ]
        if mistakes:
            raise SchemaError(mistakes)
        candidate = {"week": week, "revisions": [*(existing or {}).get("revisions", []), current]}
        validate(candidate)
        # The readback goes to stdout in BOTH paths. The Messenger turn goes through
        # `stage_command`, which is precisely the coach's path and so the one that must
        # reread; rendering it only on the CLI path would leave production without the
        # guardrail.
        turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
        if command == "preview":
            store.preview_command(commands.PLAN_REVISE,
                                  {"week": week, "revision": current}, turn_id=turn_id)
            print("SIMULATION VALIDÉE — aucun changement préparé ou enregistré.")
            print(revision_readback(week, previous, current))
            notes = _distance_notes(store, current)
            if notes:
                print(notes)
            return EXIT_OK
        if turn_id:
            store.stage_command(turn_id, f"plan:{week}:r{current['revision']}",
                                commands.PLAN_REVISE,
                                {"week": week, "revision": current})
            print("CHANGEMENT PRÉPARÉ — application en fin de tour encore requise.")
            print(revision_readback(week, previous, current))
            notes = _distance_notes(store, current)
            if notes:
                print(notes)
            return EXIT_OK
        saved = store.append_plan_revision(week, current, actor="week-plan-cli")
        print(revision_readback(week, previous, active(saved)))
        notes = _distance_notes(store, current)
        if notes:
            print(notes)
        return EXIT_OK
    plan = store.load_plan(week)
    if plan is None:
        if optional:
            print(f"{week} : aucun programme")
            return EXIT_OK
        raise NoteError(f"{week} : programme hebdomadaire absent dans PostgreSQL")
    validate(plan)
    revision = active(plan)
    stale = store.plan_needs_arbitration(week, revision["revision"])
    gates = gate_reasons(plan)
    if command == "show":
        print(as_json(plan))
    else:
        print(f"{week} : révision {revision['revision']} valide, "
              f"{len(revision['sessions'])} entrée(s)")
    if gates:
        print("\n  - ".join([f"{week} : en attente de la réponse de l'athlète :", *gates]),
              file=sys.stderr)
        return EXIT_AWAITING_GATE
    if stale:
        # The program is INTACT: it simply no longer describes the week. It is printed,
        # as for a dated check, and only the prescription waits. Raising a NoteError before
        # printing would give exit code 1 and an empty stdout, and a coach running `show`
        # would conclude the program is broken.
        print(f"{week} : arbitrage obligatoire, activité non rattachée apparue depuis "
              "la révision — le programme reste lisible, mais rien ne se prescrit "
              "avant une révision",
              file=sys.stderr)
        return EXIT_STALE
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "show", "revise", "preview"))
    parser.add_argument("--week", default=current_week())
    parser.add_argument("--optional", action="store_true")
    parser.add_argument("--input", help="JSON de revise/preview (défaut : stdin hors tour)")
    args = parser.parse_args()
    try:
        return _database_command(_database_store(), args.command, args.week, args.optional,
                                 args.input)
    except (NoteError, SchemaError, StorageError, json.JSONDecodeError,
            agent_input.AgentInputError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    entrypoint.run(main)
