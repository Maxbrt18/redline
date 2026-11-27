"""A preparation lived week by week against the engine's plan, without a model.

The trajectory tests ask the planner two questions, whatever owns the plan: the bound the
Sunday review shows for the week it prepares, and the refusals of the write gate for a
revision written that Sunday. `SkeletonPlanner` answers them from the chosen skeleton;
when the cycle version replaces the skeleton, another planner answers the same questions
and the invariants stay as they are.

The athlete is scripted: each week they run what the review showed, within what the gate
accepts, and nothing in the weeks they miss. What they ran goes into the store, so the
next review and the next gate read it, as they would in production.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from redline.analysis import weekly_dossier
from redline.model import iso_week
from redline.model.vocabulary import (
    DeadlineKind,
    DeadlineStatus,
    PlanPhase,
    RacePriority,
    RaceState,
)
from redline.service import plan_revision

#: A preparation run from where its first week stands: a half on the road, its peaks, and
#: the balanced candidate chosen.
HALF = {"distance_km": 21.1, "terrain": "road", "peak_volume_km": 50, "peak_long_run_km": 20,
        "quality_per_week": 2,
        "skeleton": {"candidate": "balanced", "specific_weeks": 6, "deload_every": 4,
                     "second_quality": "specific"}}
FIRST_WEEK = "2030-W48"
RACE_ON = dt.date(2031, 4, 6)
#: The weeks run before the first one: more than the closed weeks the start is read on.
HISTORY_WEEKS = 4
#: Seconds per kilometre of every simulated run: only the duration's presence matters.
PACE_S = 330
#: The probe's precision on a weekly volume, in kilometres.
PROBE_KM = 0.05
#: Above any weekly volume a bound can give.
PROBE_CEILING_KM = 400.0


class Store:
    """The facts the planner reads: the goal, other races, the runs; no effort, no break,
    no strength session."""

    def __init__(self, goal: dict, race_on: dt.date, others: tuple[dict, ...] = ()) -> None:
        self.goal, self.race_on, self.others = goal, race_on, others
        self.observations: list[dict] = []

    def run(self, day: dt.date, kind: str, km: float) -> None:
        self.observations.append({
            "occurred_on": day, "kind": kind, "note": "",
            "measurements": {"distance_km": {"state": "known", "value": km},
                             "duration_s": {"state": "known", "value": km * PACE_S}}})

    def observation_rows(self) -> list[dict]:
        return sorted(self.observations, key=lambda row: row["occurred_on"], reverse=True)

    def strength_history(self) -> list[dict]:
        return []

    def effort_rows(self) -> list[dict]:
        return []

    def break_rows(self) -> list[dict]:
        return []

    def calibration_rows(self) -> list[dict]:
        return []

    def deadline_rows(self) -> list[dict]:
        return [{"deadline_key": "race", "kind": DeadlineKind.RACE,
                 "status": DeadlineStatus.SCHEDULED, "priority": RacePriority.A,
                 "race_state": RaceState.ACTIVE, "due_on": self.race_on, "item": "Race",
                 "context": self.goal, "fact_owner": "athlete/goals.md", "blocks": None,
                 "completed_on": None, "starts_on": None, "ends_on": None,
                 "athlete_label": "Race"}, *self.others]


@dataclass(frozen=True)
class Bound:
    """What a week may reach: its phase, its weekly volume, its long run."""

    phase: PlanPhase
    volume_km: float
    long_run_km: float


class Planner(Protocol):
    def shown(self, store: Store, week: str) -> Bound | None:
        """The bound the Sunday review before `week` shows for it."""

    def refusals(self, store: Store, week: str, revision: dict) -> list[str]:
        """Why the gate refuses `revision` of `week`, at its `computed_at`."""


class SkeletonPlanner:
    """The chosen skeleton, as the weekly dossier reads it and as the gate applies it."""

    def shown(self, store: Store, week: str) -> Bound | None:
        found = weekly_dossier.review_bound(store, iso_week.shift(week, -1))
        if found is None:
            return None
        _, bound = found
        return Bound(bound.phase, bound.volume_km, bound.long_run_km)

    def refusals(self, store: Store, week: str, revision: dict) -> list[str]:
        unread: list[str] = []
        found = [*plan_revision.skeleton_errors(store, week, revision, None, unread.append),
                 *plan_revision.taper_errors(store, week, revision, unread.append)]
        # A check that cannot read its frame accepts everything: the simulation would
        # then measure nothing.
        assert not unread, f"the gate could not read its frame: {unread}"
        return found


def review_day(week: str) -> dt.date:
    """The Sunday the review before `week` is written on."""
    return iso_week.monday(week) - dt.timedelta(days=1)


def revision(week: str, volume_km: float, long_run_km: float, written_on: dt.date, *,
             decided: str | None = None) -> dict:
    """The first revision of `week`, written on `written_on`: the long run on Sunday and
    the rest of the volume in three easy runs; decided by `decided`, or undecided."""
    monday = iso_week.monday(week)
    easy = round((volume_km - long_run_km) / 3, 2)
    sessions = [{"id": f"easy-{day}", "kind": "run", "workout": "easy", "status": "planned",
                 "date": (monday + dt.timedelta(days=day)).isoformat(),
                 "distance_target_km": easy}
                for day in (1, 3, 5) if easy > 0]
    if long_run_km > 0:
        sessions.append({"id": "long", "kind": "run", "workout": "long", "status": "planned",
                         "date": (monday + dt.timedelta(days=6)).isoformat(),
                         "distance_target_km": long_run_km})
    found = {"revision": 1, "computed_at": f"{written_on.isoformat()}T18:00:00+01:00",
             "volume_target_km": volume_km, "sessions": sessions}
    if decided is not None:
        found["decision"] = {"by": decided}
    return found


def ceiling(planner: Planner, store: Store, week: str, written_on: dt.date) -> float:
    """The largest weekly volume the gate accepts for `week`, written on `written_on`.

    The probe carries no long run and no decision: it reads the week's volume bound, and
    the taper band binds a judgment's mean drop, which is tested on its own."""
    def accepted(km: float) -> bool:
        return not planner.refusals(store, week, revision(week, km, 0, written_on))

    assert accepted(0), f"{week}: the gate refuses an empty week"
    low, high = 0.0, PROBE_CEILING_KM
    while high - low > PROBE_KM:
        middle = (low + high) / 2
        low, high = (middle, high) if accepted(middle) else (low, middle)
    return round(low, 1)


@dataclass(frozen=True)
class Lived:
    """One week of the preparation: what the review showed, the most the gate accepted,
    what the athlete ran."""

    week: str
    shown: Bound | None
    ceiling_km: float
    run_km: float


def run_week(store: Store, week: str, volume_km: float, long_run_km: float) -> None:
    """The week as run: the long run on Sunday, the rest in two easy runs."""
    monday = iso_week.monday(week)
    long_run_km = min(long_run_km, volume_km)
    if long_run_km > 0:
        store.run(monday + dt.timedelta(days=6), "long", long_run_km)
    easy = round((volume_km - long_run_km) / 2, 2)
    if easy > 0:
        store.run(monday + dt.timedelta(days=1), "easy", easy)
        store.run(monday + dt.timedelta(days=3), "easy", easy)


@dataclass(frozen=True)
class Preparation:
    """The weeks lived, and the store as they left it."""

    store: Store
    weeks: tuple[Lived, ...]


def simulate(*, start_km: float = 30, start_long_km: float = 14,
             missed: Collection[int] = (), through: str | None = None, goal: dict = HALF,
             first: str = FIRST_WEEK, race_on: dt.date = RACE_ON,
             planner: Planner | None = None) -> Preparation:
    """The preparation from `first` to the race week, or to `through`, lived by an athlete
    who ran `start_km` a week before it (`start_long_km` the long run) and misses the
    weeks whose index is in `missed`."""
    planner = planner or SkeletonPlanner()
    store = Store(goal, race_on)
    for back in range(HISTORY_WEEKS, 0, -1):
        run_week(store, iso_week.shift(first, -back), start_km, start_long_km)
    last = through or iso_week.of(race_on)
    lived = []
    week, index = first, 0
    while week <= last:
        shown = planner.shown(store, week)
        most = ceiling(planner, store, week, review_day(week))
        run = 0.0 if index in missed or shown is None else min(shown.volume_km, most)
        run_week(store, week, run, shown.long_run_km if shown else 0)
        lived.append(Lived(week, shown, most, run))
        week, index = iso_week.shift(week, 1), index + 1
    return Preparation(store, tuple(lived))
