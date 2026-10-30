"""Deterministic compilation of a race deadline into executable milestones."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import race_countdown as wording
from redline.model import local_time


@dataclass(frozen=True)
class Stage:
    key: str
    days_before: int
    title: str
    action: str
    evidence: str
    kind: str = "race_countdown"


@dataclass(frozen=True)
class Checkpoint:
    key: str
    kind: str
    due_at: dt.datetime
    expires_at: dt.datetime
    detail: dict[str, Any]


def _stage(key: str, days_before: int, kind: str = "race_countdown") -> Stage:
    return Stage(key, days_before, *wording.STAGES[key], kind=kind)


STAGES = (
    _stage("architecture", 120),
    _stage("terrain", 90),
    # These two milestones CHECK a date, they do not impose one: a generic calendar
    # would schedule the rehearsal at a fixed offset the plan may contradict. Two
    # owners of the race calendar means a milestone pushing a session the plan
    # forbids. The plan decides the date.
    _stage("systems", 60),
    _stage("simulation", 45),
    _stage("freeze-v1", 30),
    _stage("premortem", 21, kind="premortem"),
    _stage("taper", 14),
    _stage("proof", 7),
    _stage("fuel", 3),
    _stage("brief", 1),
    _stage("race-day", 0),
)


def compile_deadline(deadline: dict) -> list[Checkpoint]:
    """Turn a race date into a stable, replayable calendar."""
    due_on = deadline["due_on"]
    if isinstance(due_on, str):
        due_on = dt.date.fromisoformat(due_on)
    race_key = str(deadline["deadline_key"])
    race_id = str(deadline.get("id") or race_key)
    timezone = local_time.zone()
    checkpoints = []
    for index, stage in enumerate(STAGES):
        day = due_on - dt.timedelta(days=stage.days_before)
        hour = 9 if stage.days_before == 0 else 18
        minute = 0 if stage.days_before == 0 else 30
        due_at = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=timezone)
        if index + 1 < len(STAGES):
            next_stage = STAGES[index + 1]
            next_day = due_on - dt.timedelta(days=next_stage.days_before)
            expires_at = dt.datetime.combine(
                next_day, dt.time(18, 29), tzinfo=timezone)
        else:
            expires_at = dt.datetime.combine(
                due_on, dt.time(23, 59), tzinfo=timezone)
        checkpoints.append(Checkpoint(
            key=f"race:{race_id}:{due_on.isoformat()}:{stage.key}",
            kind=stage.kind,
            due_at=due_at,
            expires_at=expires_at,
            detail={
                "deadline_key": race_key,
                "race": deadline.get("item") or race_key,
                "race_on": due_on.isoformat(),
                "stage": stage.key,
                "title": stage.title,
                "days_before": stage.days_before,
                "action": stage.action,
                "evidence": stage.evidence,
                "context": deadline.get("context") or {},
            },
        ))
    return checkpoints
