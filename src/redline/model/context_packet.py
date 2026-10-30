"""Bounded packet of PostgreSQL facts injected before a coaching turn."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from redline.coach.fr import context_packet as text
from redline.model import ledger as ledger_model
from redline.model import rulebook, session_kinds
from redline.model.vocabulary import ThreadStatus
from redline.storage import StorageError

if TYPE_CHECKING:
    from redline.storage import OperationalStore

log = logging.getLogger("redline")

METRICS = (
    ("distance_km", "km"), ("duration_s", "s"), ("hr_avg_bpm", text.HEART_RATE),
    ("rpe", "RPE"), ("enjoyment", text.ENJOYMENT),
    ("wbgt_c", "WBGT"),
)

def _day(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def _observation(row: dict) -> str:
    parts = [_day(row["occurred_on"]), str(row["kind"])]
    for key, label in METRICS:
        measure = (row.get("measurements") or {}).get(key) or {}
        if measure.get("state") == "known":
            parts.append(f"{label}={measure.get('value')}")
    parts.extend(ledger_model.pain_facts(row.get("pains") or {}))
    pending = [key for key, measure in (row.get("measurements") or {}).items()
               if measure.get("state") == "pending"]
    pending += ledger_model.pending_pains(row.get("pains") or {})
    if pending:
        parts.append(text.AWAITS + ",".join(sorted(pending)))
    if row.get("note"):
        parts.append("note=" + str(row["note"])[:180])
    parts.append(text.KEY + str(row["idempotency_key"]))
    return " | ".join(parts)


def render_context_packet(store: OperationalStore, depth: str, today: dt.date) -> str:
    """Render only the facts useful at this depth; N0 pays for no packet."""
    if depth == "N0":
        return ""
    lines = [text.HEADER,
             text.READ_ON.format(isoformat=today.isoformat())]

    def read(reader: Callable[..., Iterable[dict]], label: str, **kwargs: Any) -> list:
        try:
            rows = list(reader(**kwargs))
        except StorageError:
            log.warning("Context packet: %s read unavailable", label)
            lines.append(text.READ_FAILED.format(label=label))
            return []
        if not rows:
            lines.append(text.READ_EMPTY.format(label=label))
        return rows

    all_observations = read(store.observation_rows, text.OBSERVATIONS)
    observations = all_observations[:10]
    lines.append(text.RECENT_OBSERVATIONS)
    lines.extend("- " + _observation(row) for row in observations)
    # Activities can fill all ten slots: selecting the wake traces BEFORE this cut keeps
    # them from being dropped.
    morning = sorted((row for row in all_observations
                      if row["kind"] == session_kinds.WAKE_TRACE
                      and (today - dt.timedelta(days=56)).isoformat()
                      <= _day(row["occurred_on"]) < today.isoformat()),
                     key=lambda row: _day(row["occurred_on"]), reverse=True)[:8]
    if morning:
        lines.append(text.EARLIER_WAKES)
        lines.extend("- " + _observation(row) for row in morning)
    exposures = read(store.exposure_rows, text.EXPOSURES, confirmed_only=True)[:8]
    if exposures:
        lines.append(text.CONFIRMED_EXPOSURES)
        for row in exposures:
            dose = []
            if row.get("duration_min") is not None:
                dose.append(text.DOSE_MINUTES.format(duration_min=row["duration_min"]))
            if row.get("target_value") is not None:
                dose.append(text.DOSE_TARGET.format(target_value=row["target_value"], target_unit=row["target_unit"]))
            lines.append(
                text.EXPOSURE_LINE.format(day=_day(row["occurred_on"]), exposure_type=row["exposure_type"], origin=row["origin"], tolerance=row["tolerance"], dose=", ".join(dose) if dose else text.TOLERANCE_UNSTATED, observation_key=row["observation_key"]))
    recent_strength = list(reversed(read(store.strength_history, text.STRENGTH)))[:2]
    if recent_strength:
        lines.append(text.RECENT_STRENGTH)
        for session in recent_strength:
            detail = [
                str(session["performed_on"]),
                text.STRENGTH_LINE.format(workout=session.get("workout") or "?"),
            ]
            if session.get("duration_minutes") is not None:
                detail.append(text.STRENGTH_MINUTES.format(duration_minutes=session["duration_minutes"]))
            detail.extend(text.STRENGTH_PAIN + fact for fact in
                          ledger_model.strength_pain_facts(session.get("pains") or {}))
            difficult = [
                f"{exercise['exercise_slug']}={exercise['difficulty']}/10"
                for exercise in session.get("exercises", [])
                if exercise.get("difficulty") is not None
                and exercise["difficulty"] >= 8
            ]
            if difficult:
                detail.append(text.STRENGTH_HARD + ", ".join(difficult[:5]))
            if session.get("note"):
                detail.append(text.STRENGTH_NOTE + session["note"][:220])
            lines.append("- " + " | ".join(detail))
    daily = sorted(
        (row for row in read(store.daily_rows, text.DAILY) if row["checked_on"] <= today),
        key=lambda row: row["checked_on"])[-3:]
    if daily:
        lines.append(text.RECENT_DAILY)
        if daily[-1]["checked_on"] != today:
            lines.append(text.NO_DAILY_TODAY)
        lines.extend(
            text.DAILY_LINE.format(day=_day(row["checked_on"]), sleep=row["sleep"], life_load=row["life_load"], desire=row["desire"])
            + (text.DAILY_NOTE.format(note=row["note"][:120]) if row.get("note") else "")
            for row in daily)
    if depth in {"N2", "N3"}:
        threads = [row for row in read(store.thread_rows, text.THREADS) if row["status"] == ThreadStatus.OPEN][:8]
        if threads:
            lines.append(text.OPEN_THREADS)
            lines.extend(
                text.THREAD_LINE.format(thread_key=row["thread_key"], body=row["body"][:180], follow_up=_day(row["follow_up_on"]) if row["follow_up_on"] else text.NO_FOLLOW_UP)
                for row in threads)
        deadlines = [row for row in read(store.deadline_rows, text.DEADLINES) if row["status"] != "done"][:8]
        if deadlines:
            lines.append(text.ACTIVE_DEADLINES)
            lines.extend(
                f"- {row['deadline_key']} | {_day(row['due_on'])}"
                + (text.DEADLINE_DUE if _as_date(row["due_on"]) < today else "")
                + text.DEADLINE_DETAIL.format(item=row["item"], fact_owner=row["fact_owner"], blocks=row.get("blocks") or text.BLOCKS_NOTHING)
                for row in deadlines)
        capabilities = read(store.latest_capabilities, text.CAPABILITIES)
        if capabilities:
            lines.append(text.CAPABILITIES_TITLE)
            lines.extend(
                f"- {row['capability_number']} {row['slug']} | "
                f"{row['score']}/{row['scale']} {row['status']}"
                for row in capabilities)
    lines.append(text.READ_COMMANDS)
    # The rulebook in force travels with the facts: it arbitrates an outdated wording
    # reread in a file or in the conversation (`src/redline/model/rulebook.py`).
    return "\n".join(lines) + "]\n" + rulebook.render()
