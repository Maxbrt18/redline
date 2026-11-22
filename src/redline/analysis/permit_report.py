"""The easy-run permit report, read from PostgreSQL: same code for the review and the CLI.

The computation lives in `src/redline/model/permit.py`; this module only gathers the facts
(sealed choices, ledger, daily state, plans, Strava captures and their streams), and the
load of each easy run when the rulebook counts the envelope in load (`easy_load`).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from redline.analysis import easy_load, ledger
from redline.model import (
    instance,
    iso_week,
    paths,
    permit,
    prescription,
    session_kinds,
)
from redline.model.activity import Activity
from redline.model.vocabulary import SessionStatus


def _strava_id(key: str) -> int | None:
    prefix = "strava:"
    if key.startswith(prefix) and key[len(prefix):].isdigit():
        return int(key[len(prefix):])
    return None


def _plans(store: Any, weeks: set[str]
           ) -> tuple[dict[str, int | None], list[permit.Envelope], set[str]]:
    """Each planned session's duration cap, the easy runs that carry one, and the
    sessions the plan prescribed efforts on."""
    caps: dict[str, int | None] = {}
    envelopes: list[permit.Envelope] = []
    efforts: set[str] = set()
    for week in sorted(weeks):
        plan = store.load_plan(week)
        if not plan:
            continue
        for session in plan["revisions"][-1]["sessions"]:
            cap = session.get("duration_cap_min")
            caps[session["id"]] = cap
            if prescription.has_efforts(session):
                efforts.add(session["id"])
            if (cap is not None and session["workout"] == session_kinds.EASY
                    and session["status"] != SessionStatus.CANCELLED):
                envelopes.append(permit.Envelope(session["id"],
                                                 dt.date.fromisoformat(session["date"]), cap))
    return caps, envelopes, efforts


def _weeks(start: dt.date, today: dt.date) -> set[str]:
    """The ISO weeks from the permit's first one to the current one."""
    out, day = set(), start
    while day <= today:
        out.add(iso_week.of(day))
        day += dt.timedelta(weeks=1)
    return out


def _group(store: Any, activity_id: int) -> bool | None:
    try:
        count = Activity.from_json(store.strava_intake(activity_id)["distilled"]).athlete_count
    except Exception:  # no capture: the group stays unknown, never assumed
        return None
    return None if count is None else int(count) > 1


def _profile(store: Any, activity_id: int) -> easy_load.Profile | None:
    streams = store.strava_streams(activity_id)
    return None if not streams else easy_load.profile(streams)


def _loaded(store: Any, runs: list[permit.Run], groups: dict[int, bool],
            since: dt.date) -> list[permit.Run]:
    """The easy runs from `since` with their load at the reference pace of their day; the
    runs before `since` only set the reference."""
    profiles = {run.strava_id: found for run in runs
                if run.type == session_kinds.EASY and run.strava_id is not None
                for found in [_profile(store, run.strava_id)] if found is not None}
    found = easy_load.samples((run.date, profiles[run.strava_id], groups.get(run.strava_id))
                              for run in runs if run.strava_id in profiles)
    out = []
    for run in runs:
        if run.date < since:
            continue
        shape = profiles.get(run.strava_id) if run.strava_id is not None else None
        reference = easy_load.reference_mps(found, run.date)
        if shape is not None and reference:
            run = dataclasses.replace(run, load=permit.Load(
                shape.equivalent_minutes(reference), round(1000 / reference)))
        out.append(run)
    return out


def report(store: Any, today: dt.date, week: str | None = None) -> list[str]:
    """The permit block; empty when nothing is sealed or the rulebook declares no permit."""
    if permit.declared() is None:
        return []
    choices = list(store.permit_choice_rows())
    period = permit.window(choices)
    if period is None:
        return []
    # An easy run's cap is the top of the athlete's Z2: a single source, their HR bounds.
    hr_cap = instance.load(paths.vault_root()).hr_bounds[1]
    rows = ledger.parse_ledger(store)
    counts_load = permit.thresholds().raw_guard_pct is not None
    # The reference pace of the first runs reads the weeks before the permit's start.
    since = (period.start - dt.timedelta(weeks=easy_load.REFERENCE_WEEKS) if counts_load
             else period.start)
    runs = [permit.Run(row.date, row.type,
                       None if row.hours is None else row.hours * 60,
                       row.hr_valid, _strava_id(row.idempotency_key))
            for row in rows if row.date >= since]
    # The permit is judged on the WORST morning trace across all zones: one complaining
    # zone is enough to refuse it. This is the only place zones are merged, and they are
    # merged by the maximum, never by an average.
    traces: dict[dt.date, float] = {}
    for row in rows:
        if row.type == session_kinds.WAKE_TRACE and row.pains and row.date >= period.start:
            traces[row.date] = max(traces.get(row.date, 0), *row.pains.values())
    weeks = {choice["iso_week"] for choice in choices} | _weeks(period.start, today)
    groups = {run.strava_id: group for run in runs if run.type == session_kinds.EASY and run.strava_id
              for group in [_group(store, run.strava_id)] if group is not None}
    if counts_load:
        runs = _loaded(store, runs, groups, period.start)
    caps, envelopes, efforts = _plans(store, weeks)
    lines = permit.evaluate(choices, runs, traces, caps, groups, today, hr_cap=hr_cap,
                            envelopes=envelopes, efforts=efforts)
    current = permit.state(lines, store.daily_rows(), today, period)
    return permit.render(lines, current, week)
