"""A typed race (`redline fact-write deadline --kind race`): its taper and its plan.

A 10 km, a marathon and a 100-mile race do not taper alike, and one athlete may run all
three. So a race tapers as the cycle template that serves its goal says (`taper_weeks`,
`taper_cut_pct`), the template its preparation's trame was generated from; the rulebook
only supplies the taper of a race no template serves. Every reader of the taper goes
through `Race`, never the template or the rulebook.

The race also carries what its plan needs (`route_gpx`, `aid_stations`, `stop_minutes`,
`carbs_g_h`, `pace_s_per_km`): figures, typed, while the race sheet keeps the reasons. The
pace is the one chosen with the coach, never a default: without it, the plan has no
passages.

And the goal the preparation is built backwards from (`Goal`): distance and terrain,
which pick the cycle template and its taper; the climbing it asks, its time goal. The
peaks, the levers and the taper of a preparation are its template's and its cycle
version's (`redline.model.cycles`), never the race's.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from . import cycle_templates, rulebook
from .schema import Schema, field
from .vocabulary import (
    CLOSED_RACE_STATES,
    DeadlineKind,
    DeadlineStatus,
    GoalSource,
    RacePriority,
    RaceState,
    RaceTerrain,
)


def _goal_coherent(context: dict) -> list[str]:
    if (context.get("goal_time_s") is None) != (context.get("goal_source") is None):
        return ["goal_time_s and goal_source go together"]
    return []


AID_STATION_SCHEMA = Schema("aid station", [
    field("km", float, minimum=0.1, maximum=500),
    field("name", str, minimum=1, maximum=40),
])

#: The keys of a race's context the engine reads. The others describe the race for the
#: notebook and are left as they are.
CONTEXT_SCHEMA = Schema("race context", [
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
    field("distance_km", float, required=False, minimum=1, maximum=400,
          note="the race's distance, which picks the plan template"),
    field("terrain", str, required=False, choices=tuple(RaceTerrain)),
    field("dplus_m", int, required=False, minimum=0, maximum=30000),
    field("goal_time_s", int, required=False, minimum=600, maximum=360000,
          note="the time goal, in seconds; with goal_source"),
    field("goal_source", str, required=False, choices=tuple(GoalSource)),
    field("power_hike_from_pct", int, required=False, minimum=3, maximum=40,
          note="on a trail, the grade the long runs walk briskly from"),
], extra=True, check=_goal_coherent)


class DeadlineStore(Protocol):
    def deadline_rows(self) -> list[dict]: ...


@dataclass(frozen=True)
class AidStation:
    km: float
    name: str


@dataclass(frozen=True)
class Goal:
    """What the preparation is built backwards from: set in the interview, typed."""

    distance_km: float
    terrain: RaceTerrain
    dplus_m: int | None = None
    goal_time_s: int | None = None
    goal_source: GoalSource | None = None


@dataclass(frozen=True)
class Race:
    on: dt.date
    priority: RacePriority
    state: RaceState
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
    goal: Goal | None = None
    #: The context's keys that could not be read, dropped by the reading.
    unread: tuple[str, ...] = ()
    #: The deadline's key.
    key: str = ""
    #: The race's distance, said with or without its terrain: what a recovery reads.
    distance_km: float | None = None


def _goal(context: dict) -> Goal | None:
    if context.get("distance_km") is None or context.get("terrain") is None:
        return None
    return Goal(
        distance_km=float(context["distance_km"]), terrain=RaceTerrain(context["terrain"]),
        dplus_m=context.get("dplus_m"), goal_time_s=context.get("goal_time_s"),
        goal_source=GoalSource(context["goal_source"]) if context.get("goal_source") else None)


def template_of(goal: Goal | None) -> cycle_templates.Preparation | None:
    """The preparation template that serves the race's goal, from the instance's cycle
    templates or the engine's."""
    if goal is None:
        return None
    vault = rulebook.current().source.parent
    return cycle_templates.load(vault).preparation_for(goal.distance_km, goal.terrain)


def from_row(row: dict) -> Race:
    """A race as stored. A context written before its keys were typed is read anyway: an
    unreadable key costs the reader its value, never the race."""
    due = row["due_on"]
    written = row.get("context") or {}
    context = written
    if CONTEXT_SCHEMA.errors(written):
        context = {spec.name: written[spec.name] for spec in CONTEXT_SCHEMA.fields
                   if spec.name in written
                   and not Schema("race context", [spec]).errors({spec.name: written[spec.name]})}
    defaults = rulebook.current().parameters
    goal = _goal(context)
    template = template_of(goal)

    band = template.taper_cut_pct if template is not None else None
    return Race(on=due if isinstance(due, dt.date) else dt.date.fromisoformat(due),
                priority=RacePriority(row["priority"]), state=RaceState(row["race_state"]),
                taper_weeks=(template.taper_weeks if template is not None
                             else defaults.taper_weeks),
                taper_cut_min_pct=(round(band.low) if band is not None
                                   else defaults.taper_cut_min_pct),
                taper_cut_max_pct=(round(band.high) if band is not None
                                   else defaults.taper_cut_max_pct),
                name=str(row.get("athlete_label") or row.get("item") or ""),
                start_time=_clock(context.get("start_time")),
                route_gpx=context.get("route_gpx"),
                aid_stations=tuple(sorted((AidStation(float(aid["km"]), aid["name"])
                                           for aid in context.get("aid_stations") or []),
                                          key=lambda aid: aid.km)),
                stop_minutes=float(context.get("stop_minutes") or 0),
                carbs_g_h=context.get("carbs_g_h"),
                pace_s_per_km=_number(context.get("pace_s_per_km")),
                goal=goal,
                unread=tuple(sorted(set(written) - set(context))),
                key=str(row.get("deadline_key") or ""),
                distance_km=_number(context.get("distance_km")))


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
                   if row.get("kind") == DeadlineKind.RACE and row.get("due_on") is not None),
                  key=lambda race: race.on)


def active_goal(store: DeadlineStore) -> Race | None:
    """The goal the preparation is built for: the race of priority A still active, past
    or ahead. A goal whose day has passed stays the goal until its outcome is written."""
    return next((race for race in typed(store)
                 if race.priority == RacePriority.A and race.state == RaceState.ACTIVE), None)


def next_race(store: DeadlineStore, today: dt.date) -> Race | None:
    """The next active race from `today` on, whatever its priority: the one whose day and
    logistics come first."""
    return next((race for race in typed(store)
                 if race.state == RaceState.ACTIVE and race.on >= today), None)


#: The moves a race may make from each state; a closed state is left no more.
MOVES: dict[RaceState, frozenset[RaceState]] = {
    RaceState.PROPOSED: frozenset({RaceState.ACTIVE, RaceState.DROPPED}),
    RaceState.ACTIVE: frozenset({RaceState.POSTPONED, RaceState.CANCELLED, RaceState.RUN,
                                 RaceState.ABANDONED}),
    RaceState.POSTPONED: frozenset({RaceState.ACTIVE, RaceState.CANCELLED}),
}
#: The states that call for a recovery: run, or started and not finished. A race not
#: started (cancelled) calls for none.
RECOVERED_FROM = frozenset({RaceState.RUN, RaceState.ABANDONED})
#: The states a race is written in first.
OPENING = frozenset({RaceState.PROPOSED, RaceState.ACTIVE})


def move_error(before: RaceState | None, after: RaceState) -> str | None:
    """Why a race may not go from `before` (None: not written yet) to `after`, or None."""
    if before is None:
        return (None if after in OPENING else
                f"a race is written first as {' or '.join(sorted(OPENING))}, not {after}")
    if before == after or after in MOVES.get(before, frozenset()):
        return None
    allowed = sorted(MOVES.get(before, frozenset()))
    return (f"a race goes from {before} to {', '.join(allowed)}, not {after}" if allowed
            else f"a race {before} is closed: it changes state no more")


def status_of(state: RaceState) -> DeadlineStatus:
    """A race's deadline status: done once its state is closed."""
    return DeadlineStatus.DONE if state in CLOSED_RACE_STATES else DeadlineStatus.SCHEDULED
