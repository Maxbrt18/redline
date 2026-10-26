"""Le rapport du permis EF, lu depuis PostgreSQL : même code pour le bilan et la CLI.

Le calcul vit dans `scripts/model/permit.py` ; ici on ne fait que réunir les faits
(choix scellés, registre, état du jour, plans, captures Strava).
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import ledger  # noqa: E402
from scripts.model import permit  # noqa: E402


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
        count = (store.strava_intake(activity_id).get("distilled") or {}).get("nb_athletes")
    except Exception:  # capture absente : le groupe reste inconnu, jamais supposé
        return None
    return None if count is None else int(count) > 1


def report(store: Any, today: dt.date, week: str | None = None) -> list[str]:
    choices = list(store.permit_choice_rows())
    if not choices:
        return []
    rows = ledger.parse_ledger(store)
    runs = [permit.Run(row.date, row.type,
                       None if row.hours is None else row.hours * 60,
                       row.hr_valid, _strava_id(row.idempotency_key))
            for row in rows if row.date >= permit.START]
    traces: dict[dt.date, float] = {}
    for row in rows:
        if row.type == "reveil" and row.pain is not None and row.date >= permit.START:
            traces[row.date] = max(traces.get(row.date, 0), row.pain)
    weeks = {choice["iso_week"] for choice in choices}
    groups = {run.strava_id: group for run in runs if run.type == "EF" and run.strava_id
              for group in [_group(store, run.strava_id)] if group is not None}
    lines = permit.evaluate(choices, runs, traces, _caps(store, weeks), groups, today)
    current = permit.state(lines, store.daily_rows(), today)
    return permit.render(lines, current, week)
