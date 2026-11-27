"""The `redline cycle` command: the cycle in force, the trames the engine generates, and
the two the coach writes.

`show` reads the version in force, or the one `--version` names, week by week. `candidates`
generates trames from the athlete's real state: for the active goal, the preparation's
candidates; without one, a block of the general preparation at the default level.
`--kind` asks for another kind of cycle, `--level` for another general level. Today's
paces are rendered from the calibration in force (`redline.analysis.cycle_report`).

`propose` writes a candidate of the goal's preparation as a proposal, open for the
rulebook's `cycle_proposal_days`, a lower `--peak-km` if chosen: the athlete takes it,
never the coach. `level` writes the general preparation at another level from next week.
Both carry their typed decision (`--decision`, as a revision's).

    redline cycle show
    redline cycle candidates
    redline cycle candidates --kind general --level progress
    redline cycle propose --candidate balanced --decision '{"by": "judgment", ...}'
    redline cycle level --set progress --decision '{"by": "request", ...}'
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid

from redline.analysis import cycle_choice, cycle_report
from redline.coach.fr import cycle as text
from redline.model import agent_input, commands, entrypoint, rulebook
from redline.model import cycles as cycle_model
from redline.model.errors import ConfigError, InputError
from redline.model.vocabulary import CycleKind, GeneralLevel
from redline.model.weekly_plan import DECISION_SCHEMA

ACTOR = "cycle"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="the version in force, or the one named")
    show.add_argument("--version", type=uuid.UUID, default=None,
                      help="a version's id (default: the version in force)")
    listed = commands.add_parser("candidates", help="the trames generated from today's state")
    listed.add_argument("--kind", choices=tuple(CycleKind), default=None,
                        help="the kind of cycle (default: the goal's preparation, else general)")
    listed.add_argument("--level", choices=tuple(GeneralLevel), default=None,
                        help="the general preparation's level (default: the templates')")
    proposed = commands.add_parser("propose", help="a candidate proposed to the athlete")
    proposed.add_argument("--candidate", required=True,
                          help="the candidate's slug, as `cycle candidates` names it")
    proposed.add_argument("--peak-km", type=float, default=None,
                          help="a peak under the proposed one, in km a week")
    leveled = commands.add_parser("level", help="the general preparation at another level")
    leveled.add_argument("--set", dest="level", required=True, choices=tuple(GeneralLevel))
    for sub in (proposed, leveled):
        sub.add_argument("--decision", type=json.loads, required=True,
                         help="JSON: by (rule, judgment, request), rule, alternatives")
    for sub in (show, listed, proposed, leveled):
        sub.add_argument("--date", type=dt.date.fromisoformat, default=None,
                         help="reference day YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    from redline.storage import open_store

    store = open_store(ACTOR)
    today = args.date or dt.date.today()
    if args.command in ("propose", "level"):
        print(_write(store, args, today))
        return 0
    if args.command == "show":
        version = (store.cycle_version(args.version) if args.version is not None
                   else store.active_cycle())
        if version is None:
            raise InputError("no cycle version: the planner writes the first on its next pass"
                             if args.version is None else f"no cycle version {args.version}")
        lines = cycle_report.shown(store, version, today)
    else:
        lines = cycle_report.candidates(store, today,
                                        CycleKind(args.kind) if args.kind else None,
                                        GeneralLevel(args.level) if args.level else None)
    print("\n".join(lines))
    return 0


def _decision(value: object) -> dict:
    problems = DECISION_SCHEMA.errors(value) if isinstance(value, dict) else ["an object"]
    if problems:
        raise InputError("--decision: " + "; ".join(problems))
    assert isinstance(value, dict)
    return value


def _write(store, args, today: dt.date) -> str:
    """The proposal or the new level, submitted as the turn's command."""
    decision = _decision(args.decision)
    if args.command == "propose":
        days = rulebook.current().parameters.cycle_proposal_days
        if days is None:
            raise ConfigError("rulebook.toml declares no cycle_proposal_days")
        trame, race = cycle_choice.proposal(store, today, args.candidate, args.peak_km)
        expires = dt.datetime.combine(today + dt.timedelta(days=days), dt.time(23, 59),
                                      tzinfo=dt.UTC)
        kind, payload = commands.CYCLE_PROPOSE, {
            "trame": cycle_model.payload_of(trame), "decision": decision,
            "race_key": race.key, "race": race.name, "expires_at": expires.isoformat()}
        done = text.PROPOSED.format(candidate=args.candidate, race=race.name,
                                    on=expires.date())
    else:
        trame = cycle_choice.level(store, today, GeneralLevel(args.level))
        kind, payload = commands.CYCLE_LEVEL, {
            "trame": cycle_model.payload_of(trame), "decision": decision, "level": args.level}
        done = text.LEVELED.format(level=text.LEVELS[args.level], week=trame.weeks[0].label)
    submitted = store.submit(kind, payload, command_key=f"{kind}:{commands.digest(payload)}",
                             turn_id=agent_input.current_turn(), actor=ACTOR)
    return text.STAGED.format(done=done) if submitted.staged else done


if __name__ == "__main__":
    entrypoint.run(main)
