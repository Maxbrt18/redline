"""Obsidian/Git projections, regenerable from PostgreSQL."""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa

from redline.model import strength_log, vault_layout, weekly_plan
from redline.model.views import apply as apply_view

from .repository import OperationalStore


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _csv(path: Path, columns, rows) -> None:
    """Write a projected CSV atomically; the single path shared by all CSV projections.

    `csv` escapes commas and quotes in a free-text note, so rows are never concatenated
    by hand.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    _atomic(path, buffer.getvalue())


def project_daily(store: OperationalStore, root: Path) -> None:
    _csv(root / vault_layout.DAILY_CSV,
         ("date", "sommeil", "charge_vie", "envie", "note"),
         [[row["checked_on"].isoformat(), row["sleep"] or "", row["life_load"] or "",
           row["desire"] or "", row["note"] or ""] for row in store.daily_rows()])


def project_instruments(store: OperationalStore, root: Path) -> None:
    path = root / vault_layout.INSTRUMENTS_CSV
    columns = ("semaine", "date_bilan", "cmj_cm", "mollet_d_tendu", "mollet_g_tendu",
               "mollet_d_flechi", "mollet_g_flechi", "plaisir", "canari_dlr",
               "calib_ecart_dlr", "calib_ecart_fc", "sommeil",
               "charge_vie", "note")
    fields = ("iso_week", "reviewed_on", "cmj_cm", "calf_right_straight",
              "calf_left_straight", "calf_right_bent", "calf_left_bent", "enjoyment",
              "canary_pain", "pain_calibration_gap", "hr_calibration_gap", "sleep",
              "life_load", "note")
    _csv(path, columns,
         [[row[field] if row[field] is not None else "" for field in fields]
          for row in store.weekly_instrument_rows()])


def _strength_row(session: dict, exercise: dict) -> list:
    sides: dict[str, list[str]] = {"both": [], "right": [], "left": []}
    for series in exercise["sets"]:
        sides[series["side"]].append(str(series["repetitions"]))
    values = {
        "date": session["performed_on"].isoformat(), "sid": session["session_key"],
        "seance": session["workout"] or "", "slug": exercise["exercise_slug"],
        "valeurs": "|".join(sides["both"]), "droite": "|".join(sides["right"]),
        "gauche": "|".join(sides["left"]),
        "score": "" if exercise["difficulty"] is None else str(exercise["difficulty"]),
        "passe": strength_log.YES if exercise["skipped"] else "",
        "status": exercise.get("status") or "",
        "completed_slots": exercise.get("completed_slots") if exercise.get("completed_slots") is not None else "",
        "note": exercise.get("note", ""),
    }
    return [values[column] for column in strength_log.COLUMNS]


def project_strength(store: OperationalStore, root: Path) -> None:
    _csv(root / vault_layout.STRENGTH_LOG_CSV, strength_log.COLUMNS,
         [_strength_row(session, exercise) for session in store.strength_history()
          for exercise in session["exercises"]])


def _plan_projections(store: OperationalStore, root: Path):
    """What PostgreSQL says each program block must contain.

    Single source of the list: `project_plans` writes it, `plan_drift` checks it. Two
    copies of this loop would eventually disagree about which files they cover.
    """
    for path in sorted((root / vault_layout.JOURNAL).glob("20??-w??.md")):
        plan = store.load_plan(path.stem.upper())
        if plan:
            yield path, weekly_plan.render_journal(plan)
    plan = store.load_plan(weekly_plan.current_week())
    # `.exists()` like the glob above: a projection targets a file that EXISTS.
    # Without this guard `plan_drift` would read a missing path and crash instead of
    # reporting drift.
    hot_cache = root / vault_layout.HOT_CACHE
    if plan and hot_cache.exists():
        yield hot_cache, weekly_plan.render_hot_cache(plan)


def ensure_journals(store: OperationalStore, root: Path,
                    today: dt.date | None = None) -> list[Path]:
    """Create the journal for the current and next week when a program exists.

    A projection targets an existing file (see `_plan_projections`): without a journal
    the program would stay invisible in Obsidian. An existing file is never rewritten.
    """
    day = today or dt.date.today()
    created = []
    for week in (weekly_plan.current_week(day), weekly_plan.next_week(day)):
        path = root / vault_layout.journal(week)
        if path.exists() or store.load_plan(week) is None:
            continue
        _atomic(path, weekly_plan.journal_skeleton(week, day))
        created.append(path)
    return created


def project_plans(store: OperationalStore, root: Path) -> None:
    ensure_journals(store, root)
    for path, content in _plan_projections(store, root):
        apply_view(path, weekly_plan.BLOCK, content)


def plan_drift(store: OperationalStore, root: Path) -> list[str]:
    """Program blocks that no longer match PostgreSQL, without rewriting anything.

    A check that silently repairs is not a check.
    """
    return [str(path.relative_to(root))
            for path, content in _plan_projections(store, root)
            if apply_view(path, weekly_plan.BLOCK, content, check=True)]


def plan_view_count(store: OperationalStore, root: Path) -> int:
    """How many program blocks exist: the count a check is allowed to cite."""
    return sum(1 for _ in _plan_projections(store, root))


def project_all(store: OperationalStore, root: Path,
                *, finalize: Callable[[], None] | None = None) -> None:
    # Session lock: the Mac, the VPS and the Git hook never interleave two generations.
    # The outbox stays unacknowledged if a single file fails.
    with store.database.engine.connect() as lock_connection:
        lock_connection.execute(sa.text(
            "SELECT pg_advisory_lock(hashtext('redline:obsidian-projector'))"))
        try:
            token = store.projection_token()
            project_daily(store, root)
            project_instruments(store, root)
            project_strength(store, root)
            project_plans(store, root)
            if finalize is not None:
                finalize()
            store.mark_projected(token)
        finally:
            lock_connection.execute(sa.text(
                "SELECT pg_advisory_unlock(hashtext('redline:obsidian-projector'))"))
