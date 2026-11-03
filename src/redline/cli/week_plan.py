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

`variants` reads the athlete's days (`--input`, see `redline.model.week_days`) and prints the
placements of the remaining sessions those days allow, best first. It writes nothing.

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

from redline.analysis import decision_state
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
    SETTLED_STATUSES,
    active,
    as_json,
    change_evidence_errors,
    current_week,
    decision_errors,
    fuelling_errors,
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


def _taper_errors(store, week: str, revision: dict) -> list[str]:
    """The taper band, when `week` is in one. A measurement that fails costs the check,
    never the prescription."""
    from redline.analysis import ledger, taper
    try:
        frame = taper.race_window(store, week)
        rows = ledger.parse_ledger(store) if frame else []
    except Exception as exc:
        print(wording.TAPER_UNREADABLE.format(name=exc.__class__.__name__), file=sys.stderr)
        return []
    if frame is None:
        return []
    return taper.plan_errors(rows, frame, week, revision,
                             f"{week} revision {revision['revision']}")


def _pain_signal(store, today: dt.date) -> bool:
    """An effort pain above the rulebook's cap (rule n3) in the last 7 days."""
    from redline.analysis import decision_state, ledger
    from redline.model import ledger as ledger_model
    from redline.model import rulebook
    cap = rulebook.current().parameters.pain_cap
    since = today - dt.timedelta(days=decision_state.PAIN_WINDOW_DAYS)
    return any(value > cap for row in ledger.parse_ledger(store)
               if since < row.date <= today and row.type in ledger_model.EFFORT_TYPES
               for value in row.pains.values())


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
    from redline.analysis import reorganize
    from redline.model import paths, rulebook, week_days
    from redline.model import program as strength_program
    plan = store.load_plan(week)
    if plan is None:
        raise NoteError(f"{week}: weekly plan missing in PostgreSQL")
    validate(plan)
    revision = active(plan)
    request = week_days.parse(agent_input.read_json(input_path), week)
    vault = paths.vault_root()
    try:
        program = strength_program.load(vault)
        targets = strength_program.rpe_targets(vault)
    except NoteError:
        program, targets = None, {}
    parameters = rulebook.current().parameters
    today = today or dt.date.today()
    sessions = revision["sessions"]
    outcome = reorganize.reorganise(
        reorganize.items(sessions, revision.get("gates") or [], program, targets),
        week_days.week_dates(week), today, request,
        reorganize.Rules(runs_per_day=parameters.runs_per_day,
                         sessions_per_day=parameters.sessions_per_day,
                         load_margin_pct=parameters.reorganize_load_margin_pct,
                         long_run_weekdays=parameters.long_run_weekdays,
                         pain_signal=_pain_signal(store, today)),
        rest_days=frozenset(dt.date.fromisoformat(session["date"]) for session in sessions
                            if session["workout"] == "rest"),
        settled=frozenset(session["id"] for session in sessions
                          if session["status"] in SETTLED_STATUSES))
    if as_json:
        print(json.dumps({"week": week, "revision": revision["revision"],
                          **reorganize.as_dict(outcome)}, ensure_ascii=False))
    else:
        print(_readback(week, revision, outcome))
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
        expected = 1 if existing is None else len(existing["revisions"]) + 1
        if revision.get("revision") != expected:
            raise SchemaError([f"revision: expected {expected}, got {revision.get("revision")!r}"])
        current = dict(revision)
        # The timestamp belongs to the service, not the model. A model that invents the
        # time of day also invents the day the athlete is talking about, and
        # `semantic_errors` compares a dated check's due date to this field, so a wrong
        # time would disarm a guardrail. The only reliable clock in the chain is the one
        # that writes.
        current["computed_at"] = now_stamp()
        # What the coach could see is measured by the engine, at the same instant. A
        # measurement that fails costs the register one snapshot, never the prescription.
        current.pop("state_snapshot", None)
        try:
            current["state_snapshot"] = decision_state.snapshot(
                store, dt.date.fromisoformat(current["computed_at"][:10]))
        except Exception as exc:
            print(wording.SNAPSHOT_UNREADABLE.format(name=exc.__class__.__name__),
                  file=sys.stderr)
        # The incoming text is judged before the plan containing it: the revision is fed
        # back to the coach on every prescriptive turn, and title/note go to the athlete's
        # phone. Judged here and not in `validate` because history is append-only: the
        # same rule applied to the whole plan would refuse today's revision because of
        # yesterday's text.
        previous = existing["revisions"][-1] if existing else None
        mistakes = [
            *intention_errors(current, f"{week} revision {current["revision"]}"),
            *written_text_errors(current, f"{week} revision {current["revision"]}"),
            *change_evidence_errors(previous, current),
            *typed_target_errors(current, f"{week} revision {current["revision"]}"),
            *decision_errors(current, f"{week} revision {current["revision"]}"),
            *fuelling_errors(current, f"{week} revision {current["revision"]}"),
            *_taper_errors(store, week, current),
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
    parser.add_argument("command", choices=("check", "show", "revise", "preview", "variants"))
    parser.add_argument("--week", default=current_week())
    parser.add_argument("--optional", action="store_true")
    parser.add_argument("--input", help="revise/preview JSON, or the days of variants "
                                         "(default: stdin outside a turn)")
    parser.add_argument("--json", action="store_true", help="variants: print JSON")
    args = parser.parse_args()
    try:
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
