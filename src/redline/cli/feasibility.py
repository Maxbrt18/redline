"""The `redline feasibility` command: can the remaining first-time sessions still fit?"""

from __future__ import annotations

import argparse
import datetime as dt

from redline.analysis import feasibility, ledger
from redline.cli import _args
from redline.model import entrypoint
from redline.storage import open_store


def main() -> int:
    parser = argparse.ArgumentParser(description=feasibility.__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unwritten", action="append", default=[], type=_args.week,
                        metavar="YYYY-WNN",
                        help="programmed week to treat as still to be written")
    parser.add_argument("--date", type=_args.day, default=None,
                        help="reference day YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    store = open_store("feasibility")
    today = args.date or dt.date.today()
    print("\n".join(feasibility.report(store, ledger.parse_ledger(store), today,
                                       frozenset(args.unwritten))))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
