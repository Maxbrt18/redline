#!/usr/bin/env python3
"""Canonical weekly-plan card, rendered from the active persisted revision."""

from __future__ import annotations

import argparse
import datetime as dt
import os

from redline.cards import html, render
from redline.coach import dates
from redline.coach.fr import cards as wording
from redline.model import entrypoint
from redline.model.errors import ConfigError, InputError
from redline.model.weekly_plan import (
    active,
    details_of,
    gate_reasons,
    scheduled,
    validate,
)


def detail(session: dict) -> str:
    """What the session commits to, as asked of the model.

    Shared with the other renderers through `details_of`: a private copy would render
    nothing for a session whose only commitment is a duration cap.
    """
    return ", ".join(details_of(session))


def rows(sessions: list) -> str:
    by_date: dict[str, list[dict]] = {}
    for session in sessions:
        by_date.setdefault(session["date"], []).append(session)

    days = []
    for date_text in sorted(by_date):
        day_sessions = by_date[date_text]
        day = dt.date.fromisoformat(date_text)
        rendered = []
        for index, session in enumerate(day_sessions, 1):
            status = wording.DONE if session["status"] == "done" else ""
            rendered.append(
                '<li class="row">'
                f'<span class="row__index">{index:02d}</span>'
                '<div class="row__main">'
                f'<p class="row__name">{html.esc(session["title"])}{status}</p>'
                f'<p class="row__sub">{html.esc(detail(session))}</p>'
                '</div></li>')
        days.append(
            '<section class="day-group">'
            f'<h2 class="day-group__title">{html.esc(dates.weekday(day).capitalize())} {day:%d/%m}</h2>'
            f'<ol class="day-group__sessions">{"".join(rendered)}</ol>'
            '</section>')
    return '<div class="rows">' + "".join(days) + "</div>"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--today", default="")
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--html", action="store_true")
    args = parser.parse_args()
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise ConfigError("DATABASE_URL is required: the journal only holds a projection")
    from redline.storage import Database, OperationalStore
    store = OperationalStore(Database(database_url, application_name="chart-plan"))
    plan = store.load_plan(args.week)
    if plan is None:
        raise InputError(f"plan {args.week} missing in PostgreSQL")
    validate(plan)
    # `--today` dates the session FILTER and the freshness check alike: the card must not
    # decide "needs recalculation" from the clock while displaying another date.
    as_of = dt.date.fromisoformat(args.today) if args.today else None
    revision = active(plan)
    stale = gate_reasons(plan, as_of)
    if store.plan_needs_arbitration(args.week, revision["revision"]):
        stale.append("week data changed since the last computation")
    if stale:
        raise InputError("plan to recompute: " + " ; ".join(stale))
    # Descriptive view: done sessions are marked « (fait) ». Whether a session is cancelled
    # or settled is asked of the model, never reimplemented here.
    sessions = scheduled(revision["sessions"])
    if args.today:
        sessions = [session for session in sessions if session["date"] == args.today]
    body = ('<main id="card">'
            + html.header(wording.PLAN_HEADER.format(week=args.week, revision=revision["revision"]),
                          revision["reason"])
            + html.section(wording.PLAN_SECTION) + rows(sessions)
            + html.footer(wording.PLAN_FOOTER)
            + "</main>")
    render.emit(body, args.out, html_only=args.html)


if __name__ == "__main__":
    entrypoint.run(main)
