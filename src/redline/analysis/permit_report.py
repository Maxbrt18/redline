"""The easy-run permit report, read from PostgreSQL: same code for the review and the CLI.

The computation lives in `src/redline/model/permit.py`; this module only gathers the facts
(sealed choices, ledger, daily state, plans, Strava captures).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from redline.analysis import ledger
from redline.model import (
    instance,
    paths,
    permit,
    session_kinds,
)
from redline.model.activity import Activity


def _strava_id(key: str) -> int | None:
    prefix = "strava:"
    if key.startswith(prefix) and key[len(prefix):].isdigit():
        return int(key[len(prefix):])
    return None


def _caps(store: Any, weeks: set[str]) -> dict[str, int | None]:
    caps: dict[str, int | None] = {}
    for week in weeks:
        plan = store.load_plan(week)
        if not plan:
            continue
        for session in plan["revisions"][-1]["sessions"]:
            caps[session["id"]] = session.get("duration_cap_min")
    return caps


def _group(store: Any, activity_id: int) -> bool | None:
    try:
        count = Activity.from_json(store.strava_intake(activity_id)["distilled"]).athlete_count
    except Exception:  # no capture: the group stays unknown, never assumed
        return None
    return None if count is None else int(count) > 1


def report(store: Any, today: dt.date, week: str | None = None) -> list[str]:
    choices = list(store.permit_choice_rows())
    period = permit.window(choices)
    if period is None:
        return []
    # An easy run's cap is the top of the athlete's Z2: a single source, their HR bounds.
    hr_cap = instance.load(paths.vault_root()).hr_bounds[1]
    rows = ledger.parse_ledger(store)
    runs = [permit.Run(row.date, row.type,
                       None if row.hours is None else row.hours * 60,
                       row.hr_valid, _strava_id(row.idempotency_key))
            for row in rows if row.date >= period.start]
    # The permit is judged on the WORST morning trace across all zones: one complaining
    # zone is enough to refuse it. This is the only place zones are merged, and they are
    # merged by the maximum, never by an average.
    traces: dict[dt.date, float] = {}
    for row in rows:
        if row.type == session_kinds.WAKE_TRACE and row.pains and row.date >= period.start:
            traces[row.date] = max(traces.get(row.date, 0), *row.pains.values())
    weeks = {choice["iso_week"] for choice in choices}
    groups = {run.strava_id: group for run in runs if run.type == session_kinds.EASY and run.strava_id
              for group in [_group(store, run.strava_id)] if group is not None}
    lines = permit.evaluate(choices, runs, traces, _caps(store, weeks), groups, today,
                            hr_cap=hr_cap)
    current = permit.state(lines, store.daily_rows(), today, period)
    return permit.render(lines, current, week)
