"""A session whose effort accumulates, read against the athlete's own comparable ones.

A long run's session RPE grows with its duration, faster than the duration, and how fast
is the athlete's own: no fixed band holds for every duration. What a long run "should"
cost is read from the athlete's previous long runs of a comparable duration
(`comparable_duration_pct`, the latest `comparable_sessions`, both in the rulebook).
Without enough history there is no reference, and nothing is compared.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from redline.model import rulebook, session_kinds

from .ledger import Row


@dataclass(frozen=True)
class Comparable:
    """One previous session of the same kind and a comparable duration."""

    date: dt.date
    km: float | None
    minutes: int
    rpe: float

    def as_json(self) -> dict:
        return {"date": self.date.isoformat(), "km": self.km, "minutes": self.minutes,
                "rpe": self.rpe}


def comparables(rows: Sequence[Row], kind: str, minutes: float,
                before: dt.date) -> list[Comparable]:
    """The latest sessions of `kind` before `before` whose duration compares, newest first.

    Only sessions with a declared RPE count: the reference is what the athlete felt.
    Empty for a kind whose effort does not accumulate, or when the rulebook in force
    does not declare the comparison.
    """
    parameters = rulebook.current().parameters
    share, count = parameters.comparable_duration_pct, parameters.comparable_sessions
    if kind not in session_kinds.ACCUMULATING or share is None or count is None or minutes <= 0:
        return []
    found = []
    for row in sorted(rows, key=lambda item: item.date, reverse=True):
        if row.type != kind or row.date >= before or row.rpe is None or not row.hours:
            continue
        other = row.hours * 60
        if abs(other - minutes) <= max(other, minutes) * share / 100:
            found.append(Comparable(row.date, row.km, round(other), row.rpe))
        if len(found) == count:
            break
    return found


def typical_rpe(rows: Sequence[Row], kind: str, minutes: float,
                before: dt.date) -> float | None:
    """The median RPE of the comparable sessions, or None without any."""
    found = comparables(rows, kind, minutes, before)
    return statistics.median(item.rpe for item in found) if found else None
