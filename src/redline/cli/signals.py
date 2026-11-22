"""The `redline signals` command: the weak signals of the last weeks of the ledger, and
the athlete's easy HR by grade read from the kept streams."""

from __future__ import annotations

import argparse
import datetime as dt

from redline.analysis import grade_hr, ledger, signals
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
    today = dt.date.today()
    print(signals.render(rows, weeks, signals.intentional_cadence_keys(store), today,
                         grade_hr.curve_on(store, rows, today)))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
