"""The `redline ledger` command: the load gauge, its pain trend and its export."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import sys
from pathlib import Path

from redline.analysis import ledger, week_review
from redline.coach.fr import ledger as text
from redline.model import entrypoint, paths, program
from redline.model.errors import InputError
from redline.storage import open_store

# The export's column names. Pain has one column per zone, `pain_<zone>`, between `rpe`
# and `enjoyment`: whatever reads this CSV finds every series under its own name.
CSV_HEADERS = ["date", "week", "type", "km", "duration_h", "hr_avg", "cadence",
               "dplus", "dminus", "rpe", "enjoyment", "note"]


def _export_csv(rows: list[ledger.Row], zones: list[ledger.PainZone]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    pain_headers = [f"pain_{zone.slug}" for zone in zones]
    writer.writerow([*CSV_HEADERS[:10], *pain_headers, *CSV_HEADERS[10:]])
    for row in rows:
        writer.writerow([row.date, row.week, row.type, row.km,
                         row.hours and round(row.hours, 3), row.hr_avg, row.cadence,
                         row.dplus, row.dminus, row.rpe,
                         *(row.pain_at(zone.slug) for zone in zones),
                         row.enjoyment, row.note])
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=ledger.__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--week", dest="week", help="e.g. 2030-W30")
    parser.add_argument("--pain-trend", dest="pain_weeks", type=int, metavar="N")
    parser.add_argument("--csv", action="store_true", help="normalised export on stdout")
    parser.add_argument("--long-run-target", dest="target_km", type=float, metavar="KM",
                        help="long-run distance to reach before the taper")
    args = parser.parse_args()

    store = open_store("ledger-gauge")
    rows = ledger.parse_ledger(store)
    instruments = ledger.load_instruments(store)
    # Targets come from the session sheets, never from a hard-coded table.
    targets = program.rpe_targets(args.root or paths.vault_root())

    if args.csv:
        sys.stdout.write(_export_csv(rows, ledger.pain_zones(store)))
        return 0

    if args.pain_weeks:
        print(text.TREND_TITLE)
        print(text.TREND_SERIES)
        zones = ledger.pain_zones(store)
        if not zones:
            print(text.TREND_NO_ZONE)
        for zone in zones:
            print(f"{zone.label}")
            weeks = ledger.pain_trend(rows, args.pain_weeks, zone.slug)
            for week in weeks:
                print(f"  {week}")
            recent = [row for row in rows if weeks and row.week >= weeks[0].week]
            flags = ledger.esculier_check(recent, zone.slug, history=rows)
            print(text.TREND_ESCULIER)
            for flag in flags:
                print(f"    {flag}")
            if not flags:
                print(text.TREND_NOTHING)
            for line in week_review.wake_lines(rows, zone.slug, dt.date.today()):
                print(line)
            print()
        return 0

    per_week = ledger.eq_hours_per_week(rows, instruments, targets)
    if not per_week:
        print(text.EMPTY_LEDGER)
        return 0
    if args.week:
        try:
            label = ledger.normalize_week(args.week, sorted(per_week))
        except ValueError as exc:
            raise InputError(f"week refused -- {exc}") from exc
    else:
        label = max(per_week)

    print()
    for line in week_review.week_report(store, rows, instruments, targets, label, dt.date.today(),
                            target_km=args.target_km):
        print(f"  {line}" if line else "")
    print()
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
