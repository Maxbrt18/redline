"""A typed race (`redline fact-write deadline --kind race`) and the taper it asks for.

A 10 km, a marathon and a 100-mile race do not taper alike, and one athlete may run all
three. So the race carries its own taper in its `context` (`taper_weeks`,
`taper_cut_min_pct`, `taper_cut_max_pct`), and the rulebook only supplies what a race
leaves unsaid. Every reader of the taper goes through `Race`, never the rulebook.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from . import rulebook
from .schema import Schema, field

KIND = "race"


def _band_ordered(context: dict) -> list[str]:
    low, high = context.get("taper_cut_min_pct"), context.get("taper_cut_max_pct")
    if low is not None and high is not None and low > high:
        return [f"taper_cut_min_pct ({low}) is above taper_cut_max_pct ({high})"]
    return []


#: The keys of a race's context the engine reads. The others describe the race for the
#: notebook and are left as they are.
CONTEXT_SCHEMA = Schema("race context", [
    field("taper_weeks", int, required=False, minimum=1, maximum=6,
          note="weeks of taper, race week included"),
    field("taper_cut_min_pct", int, required=False, minimum=0, maximum=100,
          note="least drop of the taper's mean weekly volume, in %"),
    field("taper_cut_max_pct", int, required=False, minimum=0, maximum=100,
          note="largest drop of the taper's mean weekly volume, in %"),
], extra=True, check=_band_ordered)


class DeadlineStore(Protocol):
    def deadline_rows(self) -> list[dict]: ...


@dataclass(frozen=True)
class Race:
    on: dt.date
    status: str
    taper_weeks: int
    taper_cut_min_pct: int | None
    taper_cut_max_pct: int | None


def from_row(row: dict) -> Race:
    due = row["due_on"]
    context = row.get("context") or {}
    defaults = rulebook.current().parameters

    def said(name: str) -> int | None:
        value = context.get(name)
        return getattr(defaults, name) if value is None else int(value)

    return Race(on=due if isinstance(due, dt.date) else dt.date.fromisoformat(due),
                status=str(row.get("status") or ""),
                taper_weeks=said("taper_weeks") or defaults.taper_weeks,
                taper_cut_min_pct=said("taper_cut_min_pct"),
                taper_cut_max_pct=said("taper_cut_max_pct"))


def typed(store: DeadlineStore) -> list[Race]:
    """Every typed race, run or ahead, by date."""
    return sorted((from_row(row) for row in store.deadline_rows()
                   if row.get("kind") == KIND and row.get("due_on") is not None),
                  key=lambda race: race.on)


def upcoming(store: DeadlineStore, today: dt.date) -> Race | None:
    """The next typed race still to run, never prose."""
    ahead = [race for race in typed(store) if race.status != "done" and race.on > today]
    return ahead[0] if ahead else None
