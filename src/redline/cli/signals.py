"""The `redline signals` command: the weak signals of the last weeks of the ledger."""

from __future__ import annotations

import argparse

from redline.analysis import ledger, signals
from redline.coach.fr import signals as text
from redline.model import entrypoint
from redline.storage import open_store


def main() -> int:
    parser = argparse.ArgumentParser(description=signals.__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weeks", dest="weeks", type=int, default=4)
    args = parser.parse_args()

    store = open_store("signals")
    rows = ledger.parse_ledger(store)
    weeks = signals.recent_weeks(rows, args.weeks)
    if not weeks:
        print(text.EMPTY_LEDGER)
        return 1
    print(signals.render(rows, weeks, signals.intentional_cadence_keys(store)))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
