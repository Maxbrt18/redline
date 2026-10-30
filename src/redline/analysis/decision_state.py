"""What the coach could see when it decided a revision, computed by the engine.

A decision can only be compared with another taken in a comparable state. The model
never writes this snapshot: it is computed when the revision is written, from the
ledger and the daily check-ins, so two revisions carry the same measures.
"""

from __future__ import annotations

import datetime as dt

from . import ledger

PAIN_WINDOW_DAYS = 7
#: A daily check-in older than this no longer describes the day of the decision.
DAILY_FRESH_DAYS = 2
DAILY_FIELDS = ("sleep", "life_load", "desire")


def snapshot(store: ledger.LedgerStore, today: dt.date) -> dict:
    rows = [row for row in ledger.parse_ledger(store) if row.date <= today]
    year, week, _ = today.isocalendar()
    label = f"{year}-W{week:02d}"
    volume = ledger.week_volume([row for row in rows if row.week == label])
    since = today - dt.timedelta(days=PAIN_WINDOW_DAYS)
    pains: dict[str, float] = {}
    for row in rows:
        if row.date > since:
            for zone, value in row.pains.items():
                pains[zone] = max(pains.get(zone, value), value)
    state: dict = {"as_of": today.isoformat(), "week_km_done": round(volume.km_run, 1),
                   "pain_max_7d": pains}
    fresh = [row for row in store.daily_rows()
             if today - dt.timedelta(days=DAILY_FRESH_DAYS) <= row["checked_on"] <= today]
    if fresh:
        latest = fresh[-1]
        state["daily"] = {"on": latest["checked_on"].isoformat(),
                          **{name: latest[name] for name in DAILY_FIELDS if latest[name]}}
    return state
