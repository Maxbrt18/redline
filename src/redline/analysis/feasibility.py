#!/usr/bin/env python3
"""Backward feasibility of the plan, read from PostgreSQL: same code for the review and the CLI.

The computation lives in `src/redline/model/transitions.py`; this module gathers the facts:
the typed race (`redline fact-write deadline --kind race`), the active revisions, the transitions
done, the longest run of the last 30 days. Nothing is written.

    redline feasibility                  # from today
    redline feasibility --unwritten 2030-W41  # treat W41 as still to be written

`--unwritten` explores an already scheduled week: without it, a week that has an active
revision is frozen on its novelties (typed transition, deduced long-run step).
The verdict is informational: it refuses nothing and decides nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from typing import Any

from redline.analysis import ledger
from redline.coach.fr import transitions as text
from redline.model import (
    rulebook,
    session_kinds,
    transitions,
)
from redline.model.errors import ConfigError


def novelties(sessions: list[dict], rows: list, as_of: dt.date) -> tuple[str, ...]:
    """The novelties a revision carries: its typed transitions, and a long-run step
    when a long run exceeds the longest of the 30 days before it."""
    found = [transition.kind for transition in transitions.opened(sessions, as_of)]
    for session in sessions:
        if session.get("workout") != session_kinds.LONG or session.get("status") == "cancelled":
            continue
        planned = session.get("distance_cap_km") or session.get("distance_target_km")
        day = transitions.day_of(session["date"])
        longest = ledger.longest_recent(rows, day)
        if planned and longest and float(planned) > longest + rulebook.current().parameters.stall_tolerance_km:
            found.append(transitions.LONG_RUN_STEP)
            break
    return tuple(found)


def build_problem(store: Any, rows: list, today: dt.date,
                  free: frozenset[str] = frozenset()) -> transitions.Problem | None:
    race_on = ledger.next_race_date(store, today)
    if race_on is None:
        return None
    book = rulebook.current()
    race_week = transitions.week_of(race_on)
    taper = tuple(transitions.shift(race_week, -offset)
                  for offset in reversed(range(book.parameters.taper_weeks)))
    weeks: list[str] = []
    week = transitions.week_of(today)
    while week < taper[0]:
        weeks.append(week)
        week = transitions.shift(week, 1)
    plans = store.active_plans()
    done = sorted({session["transition"] for revision in plans.values()
                   for session in revision["sessions"]
                   if session.get("transition") and session.get("status") == "done"})
    fixed = {plan_week: novelties(revision["sessions"], rows, today)
             for plan_week, revision in plans.items()
             if plan_week in weeks and plan_week not in free}
    return transitions.Problem(
        race_on=race_on, weeks=tuple(weeks),
        assimilation=frozenset(book.parameters.assimilation_weeks), taper=taper,
        fixed=fixed, required=tuple(kind for kind in book.race_firsts if kind not in done),
        done=tuple(kind for kind in book.race_firsts if kind in done))


def report(store: Any, rows: list, today: dt.date,
           free: frozenset[str] = frozenset()) -> list[str]:
    problem = build_problem(store, rows, today, free)
    if problem is None:
        return [text.NO_RACE]
    return transitions.render(transitions.diagnose(problem))


def _week(text: str) -> str:
    if not re.fullmatch(r"20\d{2}-W\d{2}", text):
        raise argparse.ArgumentTypeError(f"week expected as YYYY-WNN, got {text!r}")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--unwritten", action="append", default=[], type=_week,
                        metavar="AAAA-WNN",
                        help="programmed week to treat as still to be written")
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None,
                        help="reference day YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise ConfigError("DATABASE_URL is required: plans live in PostgreSQL")
    from redline.storage import Database, OperationalStore
    store = OperationalStore(Database(url, application_name="feasibility"))
    today = args.date or dt.date.today()
    print("\n".join(report(store, ledger.parse_ledger(store), today,
                           frozenset(args.unwritten))))
    return 0

