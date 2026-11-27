"""The `redline cycle` command: the cycle in force and the trames the engine generates,
read-only.

`show` reads the version in force, or the one `--version` names, week by week. `candidates`
generates trames from the athlete's real state: for the active goal, the preparation's
candidates; without one, a block of the general preparation at the default level.
`--kind` asks for another kind of cycle, `--level` for another general level. Today's
paces are rendered from the calibration in force (`redline.analysis.cycle_report`);
nothing is written.

    redline cycle show
    redline cycle candidates
    redline cycle candidates --kind general --level progress
"""

from __future__ import annotations

import argparse
import datetime as dt
import uuid

from redline.analysis import cycle_report
from redline.model import entrypoint
from redline.model.errors import InputError
from redline.model.vocabulary import CycleKind, GeneralLevel

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
    for sub in (show, listed):
        sub.add_argument("--date", type=dt.date.fromisoformat, default=None,
                         help="reference day YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    from redline.storage import open_store

    store = open_store(ACTOR)
    today = args.date or dt.date.today()
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


if __name__ == "__main__":
    entrypoint.run(main)
