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
from scripts.model import (  # noqa: E402
    instance,
    paths,
    permit,
    session_kinds,
)
from scripts.model.activity import Activity  # noqa: E402


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
    except Exception:  # capture absente : le groupe reste inconnu, jamais supposé
        return None
    return None if count is None else int(count) > 1


def report(store: Any, today: dt.date, week: str | None = None) -> list[str]:
    choices = list(store.permit_choice_rows())
    period = permit.window(choices)
    if period is None:
        return []
    # Le plafond d'une EF est le haut de la Z2 de l'athlète : une seule source, ses FC.
    hr_cap = instance.load(paths.vault_root()).hr_bounds[1]
    rows = ledger.parse_ledger(store)
    runs = [permit.Run(row.date, row.type,
                       None if row.hours is None else row.hours * 60,
                       row.hr_valid, _strava_id(row.idempotency_key))
            for row in rows if row.date >= period.start]
    # Le permis se juge sur la PIRE trace du matin, toutes zones confondues : une zone qui
    # proteste suffit à le refuser. C'est le seul endroit où les zones se rejoignent, et
    # elles s'y rejoignent par le maximum, jamais par une moyenne.
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
