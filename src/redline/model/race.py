"""A typed race (`redline fact-write deadline --kind race`): its taper and its plan.

A 10 km, a marathon and a 100-mile race do not taper alike, and one athlete may run all
three. So the race carries its own taper in its `context` (`taper_weeks`,
`taper_cut_min_pct`, `taper_cut_max_pct`), and the rulebook only supplies what a race
leaves unsaid. Every reader of the taper goes through `Race`, never the rulebook.

The race also carries what its plan needs (`route_gpx`, `aid_stations`, `stop_minutes`,
`carbs_g_h`, `pace_s_per_km`): figures, typed, while the race sheet keeps the reasons. The
pace is the one chosen with the coach, never a default: without it, the plan has no
passages.

And the goal the preparation is built backwards from (`Goal`): distance and terrain,
which pick the plan template and, when the race says nothing, its taper; the peak weekly
volume and long run, the quality sessions a week and the families allowed, set in the
interview with the athlete; and the skeleton chosen among the candidates
(`redline.model.plan_skeleton`), by its levers.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from . import plan_templates, rulebook
from .schema import Schema, field
from .vocabulary import (
    CLOSED_RACE_STATES,
    DeadlineKind,
    DeadlineStatus,
    GoalSource,
    PlanPhase,
    RacePriority,
    RaceState,
    RaceTerrain,
)


def _band_ordered(context: dict) -> list[str]:
    low, high = context.get("taper_cut_min_pct"), context.get("taper_cut_max_pct")
    problems = []
    if low is not None and high is not None and low > high:
        problems.append(f"taper_cut_min_pct ({low}) is above taper_cut_max_pct ({high})")
    if (context.get("goal_time_s") is None) != (context.get("goal_source") is None):
        problems.append("goal_time_s and goal_source go together")
    return problems


SECOND_QUALITY = (str(PlanPhase.DEVELOPMENT), str(PlanPhase.SPECIFIC))

#: The skeleton chosen among the candidates, by the levers that make it.
SKELETON_SCHEMA = Schema("race skeleton", [
    field("candidate", str, pattern=r"[a-z][a-z-]{0,39}", note="the candidate's slug"),
    field("specific_weeks", int, minimum=1, maximum=20),
    field("deload_every", int, minimum=2, maximum=6),
    field("second_quality", str, choices=SECOND_QUALITY,
          note="the phase whose first week brings the second quality session"),
])


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
    field("distance_km", float, required=False, minimum=1, maximum=400,
          note="the race's distance, which picks the plan template"),
    field("terrain", str, required=False, choices=tuple(RaceTerrain)),
    field("dplus_m", int, required=False, minimum=0, maximum=30000),
    field("goal_time_s", int, required=False, minimum=600, maximum=360000,
          note="the time goal, in seconds; with goal_source"),
    field("goal_source", str, required=False, choices=tuple(GoalSource)),
    field("quality_per_week", int, required=False, minimum=0, maximum=4,
          note="most quality sessions a week of the preparation; within rule n1"),
    field("peak_volume_km", float, required=False, minimum=5, maximum=300,
          note="the weekly running volume the preparation peaks at"),
    field("peak_long_run_km", float, required=False, minimum=3, maximum=120,
          note="the long run the preparation peaks at"),
    field("families", list, required=False, maximum=len(plan_templates.QUALITY_WORKOUTS),
          item=field("workout", str, choices=plan_templates.QUALITY_WORKOUTS),
          note="the quality workouts the athlete may run; absent, the template's"),
    field("power_hike_from_pct", int, required=False, minimum=3, maximum=40,
          note="on a trail, the grade the long runs walk briskly from"),
    field("skeleton", dict, required=False, schema=SKELETON_SCHEMA),
], extra=True, check=_band_ordered)


class DeadlineStore(Protocol):
    def deadline_rows(self) -> list[dict]: ...


@dataclass(frozen=True)
class AidStation:
    km: float
    name: str


@dataclass(frozen=True)
class Skeleton:
    """The levers of the chosen skeleton."""

    candidate: str
    specific_weeks: int
    deload_every: int
    second_quality: PlanPhase


@dataclass(frozen=True)
class Goal:
    """What the preparation is built backwards from: set in the interview, typed."""

    distance_km: float
    terrain: RaceTerrain
    peak_volume_km: float | None = None
    peak_long_run_km: float | None = None
    quality_per_week: int | None = None
    families: tuple[str, ...] | None = None
    dplus_m: int | None = None
    goal_time_s: int | None = None
    goal_source: GoalSource | None = None
    power_hike_from_pct: int | None = None
    skeleton: Skeleton | None = None


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
    #: The context names a chosen skeleton, readable or not.
    declares_skeleton: bool = False


def _goal(context: dict) -> Goal | None:
    if context.get("distance_km") is None or context.get("terrain") is None:
        return None
    chosen = context.get("skeleton")
    families = context.get("families")
    return Goal(
        distance_km=float(context["distance_km"]), terrain=RaceTerrain(context["terrain"]),
        peak_volume_km=_number(context.get("peak_volume_km")),
        peak_long_run_km=_number(context.get("peak_long_run_km")),
        quality_per_week=context.get("quality_per_week"),
        families=None if families is None else tuple(families),
        dplus_m=context.get("dplus_m"), goal_time_s=context.get("goal_time_s"),
        goal_source=GoalSource(context["goal_source"]) if context.get("goal_source") else None,
        power_hike_from_pct=context.get("power_hike_from_pct"),
        skeleton=None if not chosen else Skeleton(
            chosen["candidate"], chosen["specific_weeks"], chosen["deload_every"],
            PlanPhase(chosen["second_quality"])))


def template_of(goal: Goal | None) -> plan_templates.Template | None:
    """The template that serves the race's goal, from the instance's templates."""
    if goal is None:
        return None
    vault = rulebook.current().source.parent
    return plan_templates.for_race(plan_templates.load(vault), goal.distance_km, goal.terrain)


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

    def said(name: str) -> int | None:
        value = context.get(name)
        return getattr(defaults, name) if value is None else int(value)

    # A race's own taper first, then its template's, by distance, then the rulebook's.
    taper_weeks = (int(context["taper_weeks"]) if context.get("taper_weeks") is not None
                   else template.taper_weeks if template is not None else defaults.taper_weeks)
    return Race(on=due if isinstance(due, dt.date) else dt.date.fromisoformat(due),
                priority=RacePriority(row["priority"]), state=RaceState(row["race_state"]),
                taper_weeks=taper_weeks,
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
                pace_s_per_km=_number(context.get("pace_s_per_km")),
                goal=goal,
                unread=tuple(sorted(set(written) - set(context))),
                declares_skeleton=bool(written.get("skeleton")))


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
