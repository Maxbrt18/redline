"""A typed race (`redline fact-write deadline --kind race`): its taper and its plan.

A 10 km, a marathon and a 100-mile race do not taper alike, and one athlete may run all
three. So the race carries its own taper in its `context` (`taper_weeks`,
`taper_cut_min_pct`, `taper_cut_max_pct`), and the rulebook only supplies what a race
leaves unsaid. Every reader of the taper goes through `Race`, never the rulebook.

The race also carries what its plan needs (`route_gpx`, `aid_stations`, `stop_minutes`,
`carbs_g_h`, `pace_s_per_km`): figures, typed, while the race sheet keeps the reasons. The
pace is the one chosen with the coach, never a default: without it, the plan has no
passages.
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


AID_STATION_SCHEMA = Schema("aid station", [
    field("km", float, minimum=0.1, maximum=500),
    field("name", str, minimum=1, maximum=40),
])

#: The keys of a race's context the engine reads. The others describe the race for the
#: notebook and are left as they are.
CONTEXT_SCHEMA = Schema("race context", [
    field("taper_weeks", int, required=False, minimum=1, maximum=6,
          note="weeks of taper, race week included"),
    field("taper_cut_min_pct", int, required=False, minimum=0, maximum=100,
          note="least drop of the taper's mean weekly volume, in %"),
    field("taper_cut_max_pct", int, required=False, minimum=0, maximum=100,
          note="largest drop of the taper's mean weekly volume, in %"),
    field("route_gpx", str, required=False, pattern=r"[\w./-]+\.gpx",
          note="the route, a GPX path relative to the vault root"),
    field("aid_stations", list, required=False, maximum=30,
          item=field("aid station", dict, schema=AID_STATION_SCHEMA)),
    field("stop_minutes", float, required=False, minimum=0, maximum=30,
          note="minutes stopped at each aid station"),
    field("carbs_g_h", int, required=False, minimum=1, maximum=150,
          note="carbohydrate per hour of running, in g"),
    field("pace_s_per_km", float, required=False, minimum=150, maximum=900,
          note="flat-equivalent pace chosen with the coach, in s/km"),
], extra=True, check=_band_ordered)


class DeadlineStore(Protocol):
    def deadline_rows(self) -> list[dict]: ...


@dataclass(frozen=True)
class AidStation:
    km: float
    name: str


@dataclass(frozen=True)
class Race:
    on: dt.date
    status: str
    taper_weeks: int
    taper_cut_min_pct: int | None
    taper_cut_max_pct: int | None
    name: str = ""
    start_time: dt.time | None = None
    route_gpx: str | None = None
    aid_stations: tuple[AidStation, ...] = ()
    stop_minutes: float = 0.0
    carbs_g_h: int | None = None
    pace_s_per_km: float | None = None


def from_row(row: dict) -> Race:
    """A race as stored. A context written before its keys were typed is read anyway: an
    unreadable key costs the reader its value, never the race."""
    due = row["due_on"]
    context = row.get("context") or {}
    if CONTEXT_SCHEMA.errors(context):
        context = {spec.name: context[spec.name] for spec in CONTEXT_SCHEMA.fields
                   if spec.name in context
                   and not Schema("race context", [spec]).errors({spec.name: context[spec.name]})}
    defaults = rulebook.current().parameters

    def said(name: str) -> int | None:
        value = context.get(name)
        return getattr(defaults, name) if value is None else int(value)

    return Race(on=due if isinstance(due, dt.date) else dt.date.fromisoformat(due),
                status=str(row.get("status") or ""),
                taper_weeks=said("taper_weeks") or defaults.taper_weeks,
                taper_cut_min_pct=said("taper_cut_min_pct"),
                taper_cut_max_pct=said("taper_cut_max_pct"),
                name=str(row.get("athlete_label") or row.get("item") or ""),
                start_time=_clock(context.get("start_time")),
                route_gpx=context.get("route_gpx"),
                aid_stations=tuple(sorted((AidStation(float(aid["km"]), aid["name"])
                                           for aid in context.get("aid_stations") or []),
                                          key=lambda aid: aid.km)),
                stop_minutes=float(context.get("stop_minutes") or 0),
                carbs_g_h=context.get("carbs_g_h"),
                pace_s_per_km=_number(context.get("pace_s_per_km")))


def _clock(value: object) -> dt.time | None:
    try:
        return dt.time.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def typed(store: DeadlineStore) -> list[Race]:
    """Every typed race, run or ahead, by date."""
    return sorted((from_row(row) for row in store.deadline_rows()
                   if row.get("kind") == KIND and row.get("due_on") is not None),
                  key=lambda race: race.on)


def upcoming(store: DeadlineStore, today: dt.date) -> Race | None:
    """The next typed race still to run, never prose."""
    ahead = [race for race in typed(store) if race.status != "done" and race.on > today]
    return ahead[0] if ahead else None
