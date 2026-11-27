"""The `redline plan-skeleton` command: the goal's plan, built backwards by the engine.

Without a choice it prints the race's chosen skeleton, computed again from today, or the
engine's candidates when none is chosen yet: each week's phase, deload, volume, long run,
quality sessions and their families, and the risks of each candidate. The goal comes
from the race's context (`redline fact-write deadline --patch --context`), set in the
interview with the athlete.

`--choose <candidate>` keeps that candidate's levers in the race's context: from then on
the weekly revisions are held to its bounds, and every change goes through a lever and a
new computation, never a week edited by hand.

    redline plan-skeleton
    redline plan-skeleton --choose balanced
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from redline.analysis import plan_engine
from redline.coach.fr import plan_skeleton as text
from redline.model import agent_input, commands, entrypoint
from redline.model.errors import InputError

ACTOR = "plan-skeleton"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--choose", default=None, metavar="CANDIDATE",
                        help="keep this candidate's levers in the race's context")
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None,
                        help="reference day YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    from redline.storage import open_store

    store = open_store(ACTOR)
    today = args.date or dt.date.today()
    if args.choose is None:
        print("\n".join(plan_engine.report(store, today)))
        return 0
    found, skeletons = plan_engine.candidates(store, today)
    picked = next((skeleton for skeleton in skeletons if skeleton.candidate == args.choose),
                  None)
    if picked is None:
        raise InputError(text.UNKNOWN_CANDIDATE.format(
            candidate=args.choose, known=", ".join(item.candidate for item in skeletons)))
    from redline.cli import fact_write

    key = plan_engine.race_key(store, found.race)
    context = {"skeleton": {"candidate": picked.candidate,
                            "specific_weeks": picked.levers.specific_weeks,
                            "deload_every": picked.levers.deload_every,
                            "second_quality": str(picked.levers.second_quality)}}
    patch = fact_write.parser().parse_args(
        ["deadline", "--key", key, "--patch", "--context", json.dumps(context)])
    entity_key, kind, payload = fact_write.payload_of(
        patch, fact_write.existing_row(store, "deadline", key))
    store.submit(kind, payload, command_key=f"{entity_key}:{commands.digest(payload)}",
                 turn_id=agent_input.current_turn(), actor=ACTOR)
    print(text.CHOSEN_STAGED.format(candidate=picked.candidate, race_on=found.race.on,
                                    entity_key=entity_key))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
