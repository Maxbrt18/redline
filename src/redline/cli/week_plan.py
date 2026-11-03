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

`propose` checks one to three full revisions the athlete asked for from the notebook and
keeps them as proposals: none prescribes anything until the athlete takes one.

`variants` reads the athlete's days (`--input`, see `redline.model.week_days`) and prints the
placements of the remaining sessions those days allow, best first. `days` prints the days
already known for the week: habits and days stated ahead. Neither writes anything.

Confusing 2 with 1 suppresses the morning message on check days. Confusing 3 with 1 is
worse: the CLI reports "invalid" with an EMPTY stdout while the service reports "stale,
you may answer", so the coach concludes the program is broken although it is in its
prompt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from redline.coach import dates
from redline.coach.fr import plan as wording
from redline.coach.fr import reorganize as reorganize_text
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
    current_week,
    gate_reasons,
    now_stamp,
    revision_readback,
    validate,
)
from redline.service import plan_revision
from redline.storage import StorageError


def _distance_notes(store, revision: dict) -> str:
    """The gap to the rule n8 window for each upcoming session (informative)."""
    from redline.analysis import ledger
    from redline.model import rulebook
    days = rulebook.current().parameters.transition_lookback_days
    try:
        rows = ledger.parse_ledger(store)
    except Exception as exc:
        return wording.GAP_UNREADABLE.format(days=days, name=exc.__class__.__name__)
    # The revision timestamp is the service's (`now_stamp`): it is the day "upcoming" is
    # judged from, not the clock of the machine rereading.
    written_on = dt.date.fromisoformat(str(revision["computed_at"])[:10])
    notes = ledger.transition_notes(rows, revision["sessions"], written_on)
    if not notes:
        return ""
    return "\n".join([wording.GAP_TITLE.format(days=days),
                      *(f"  - {note}" for note in notes)])


def _day(date: dt.date) -> str:
    return f"{dates.weekday(date, 'abbreviated')} {date:%d/%m}"


def _readback(week: str, revision: dict, outcome) -> str:
    titles = {session["id"]: session["title"] for session in revision["sessions"]}
    lines = [reorganize_text.HEADER.format(week=week, revision=revision["revision"])]
    if not outcome.variants:
        lines.append(reorganize_text.NONE)
    for number, variant in enumerate(outcome.variants, 1):
        parts = []
        if variant.moves:
            parts.append(reorganize_text.MOVED_ONE if len(variant.moves) == 1
                         else reorganize_text.MOVED_MANY.format(count=len(variant.moves)))
        if variant.dropped:
            parts.append(reorganize_text.DROPPED_ONE if len(variant.dropped) == 1
                         else reorganize_text.DROPPED_MANY.format(count=len(variant.dropped)))
        lines.append(reorganize_text.VARIANT.format(
            number=number, summary=", ".join(parts) or reorganize_text.UNCHANGED))
        lines += [reorganize_text.MOVE.format(title=titles[move.session], before=_day(move.before),
                                              after=_day(move.after)) for move in variant.moves]
        lines += [reorganize_text.DROP.format(title=titles[item]) for item in variant.dropped]
        if variant.carried:
            placed = dict(variant.placement)
            lines.append(reorganize_text.CARRIED.format(
                day=_day(placed[variant.carried]), title=titles[variant.carried])
                + (reorganize_text.AROUND.format(minutes=variant.around_min)
                   if variant.around_min else ""))
        lines += [reorganize_text.REMARKS[note.remark].format(
            title=titles.get(note.session or "", ""), day=_day(note.date) if note.date else "",
            value=note.value) for note in variant.notes]
    if outcome.others:
        lines.append(reorganize_text.OTHERS.format(count=outcome.others))
    if outcome.held_back:
        lines.append(reorganize_text.HELD_BACK.format(
            titles=", ".join(titles[item] for item in outcome.held_back)))
    return "\n".join(lines)


def _variants(store, week: str, input_path: str | None, as_json: bool,
              today: dt.date | None = None) -> int:
    from redline.analysis import reorganize as search
    from redline.service import reorganize
    plan = store.load_plan(week)
    if plan is None:
        raise NoteError(f"{week}: weekly plan missing in PostgreSQL")
    validate(plan)
    request = reorganize.request_of(store, week, agent_input.read_json(input_path))
    today = today or dt.date.today()
    computation = reorganize.compute(store, plan, week, request, today,
                                     reorganize.pain_signal(store, today))
    if as_json:
        print(json.dumps({"week": week, "revision": computation.revision["revision"],
                          **search.as_dict(computation.outcome)}, ensure_ascii=False))
    else:
        print(_readback(week, computation.revision, computation.outcome))
    return EXIT_OK


def _propose(store, week: str, input_path: str | None) -> int:
    from redline.model import weekly_plan
    from redline.service import reorganize
    based_on, proposals = reorganize.checked_proposals(
        store, week, agent_input.read_json(input_path), now_stamp(),
        warn=lambda line: print(line, file=sys.stderr))
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
    if turn_id:
        store.stage_command(turn_id, f"propose:{week}:r{based_on}", commands.PLAN_PROPOSE,
                            {"week": week, "based_on": based_on, "proposals": proposals})
        print(reorganize_text.PROPOSED_STAGED.format(week=week, based_on=based_on))
    else:
        store.add_proposals(week, based_on, proposals, actor="week-plan-cli")
        print(reorganize_text.PROPOSED.format(week=week, based_on=based_on))
    current = active(store.load_plan(week))
    for number, proposal in enumerate(proposals, 1):
        print(reorganize_text.PROPOSAL.format(number=number, title=proposal["title"]))
        for line in weekly_plan.revision_diff(current, proposal["revision"]):
            print("     " + line)
    return EXIT_OK


EXIT_OK = 0
EXIT_INVALID = 1
EXIT_AWAITING_GATE = 2
EXIT_STALE = 3


def _database_store():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise ConfigError("DATABASE_URL is required: the journal only holds a projection")
    from redline.storage import Database, OperationalStore
    return OperationalStore(Database(url, application_name="week-plan"))


def _database_command(store, command: str, week: str, optional: bool,
                      input_path: str | None = None) -> int:
    if command in {"revise", "preview"}:
        # A coach turn hands its JSON over via `--input`, never `<`: see
        # `src/redline/model/agent_input.py`. stdin remains the operator-launch path.
        revision = agent_input.read_json(input_path)
        existing = store.load_plan(week)
        previous, current = plan_revision.checked(
            store, week, revision, existing, stamp=now_stamp(),
            warn=lambda line: print(line, file=sys.stderr))
        # The readback goes to stdout in BOTH paths. The Messenger turn goes through
        # `stage_command`, which is precisely the coach's path and so the one that must
        # reread; rendering it only on the CLI path would leave production without the
        # guardrail.
        turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip()
        if command == "preview":
            store.preview_command(commands.PLAN_REVISE,
                                  {"week": week, "revision": current}, turn_id=turn_id)
            print(wording.PREVIEW_VALID)
            print(revision_readback(week, previous, current))
            notes = _distance_notes(store, current)
            if notes:
                print(notes)
            return EXIT_OK
        if turn_id:
            store.stage_command(turn_id, f"plan:{week}:r{current['revision']}",
                                commands.PLAN_REVISE,
                                {"week": week, "revision": current})
            print(wording.CHANGE_STAGED)
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
            print(wording.NO_PLAN.format(week=week))
            return EXIT_OK
        raise NoteError(f"{week}: weekly plan missing in PostgreSQL")
    validate(plan)
    revision = active(plan)
    stale = store.plan_needs_arbitration(week, revision["revision"])
    gates = gate_reasons(plan)
    if command == "show":
        print(as_json(plan))
    else:
        print(wording.PLAN_VALID.format(week=week, revision=revision["revision"],
                                        sessions=len(revision["sessions"])))
    if gates:
        print("\n  - ".join([wording.AWAITING_ATHLETE.format(week=week), *gates]),
              file=sys.stderr)
        return EXIT_AWAITING_GATE
    if stale:
        # The program is INTACT: it simply no longer describes the week. It is printed,
        # as for a dated check, and only the prescription waits. Raising a NoteError before
        # printing would give exit code 1 and an empty stdout, and a coach running `show`
        # would conclude the program is broken.
        print(wording.ARBITRATION_REQUIRED.format(week=week),
              file=sys.stderr)
        return EXIT_STALE
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",
                        choices=("check", "show", "revise", "preview", "variants", "days",
                                 "propose"))
    parser.add_argument("--week", default=current_week())
    parser.add_argument("--optional", action="store_true")
    parser.add_argument("--input", help="revise/preview JSON, or the days of variants "
                                         "(default: stdin outside a turn)")
    parser.add_argument("--json", action="store_true", help="variants: print JSON")
    args = parser.parse_args()
    try:
        if args.command == "propose":
            return _propose(_database_store(), args.week, args.input)
        if args.command == "days":
            from redline.service import reorganize
            print(reorganize.days_readback(_database_store(), args.week))
            return EXIT_OK
        if args.command == "variants":
            return _variants(_database_store(), args.week, args.input, args.json)
        return _database_command(_database_store(), args.command, args.week, args.optional,
                                 args.input)
    except (NoteError, SchemaError, StorageError, json.JSONDecodeError,
            agent_input.AgentInputError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    entrypoint.run(main)
