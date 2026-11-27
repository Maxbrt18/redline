"""Which cycle follows the active one, and the trame of the next.

The planner keeps one cycle active (`redline.model.cycles`): this module says, from the
active version, the races, the declared breaks and where the athlete stands, whether a
new cycle starts, of which kind and why. It reads arguments only.

- No active cycle: a recovery when a race was run within its recovery, else the general
  preparation at the default level; the first version an instance gets is its takeover.
- Whatever the cycle, a declared break in progress, or a start under the rebuild's floor,
  calls for a rebuild; a rebuild in progress is not started again. The weeks a cycle
  planned low (a taper, a deload, a recovery, a rebuild) are not a start, and a recovery
  reads no floor: the weeks around a race are low by design.
- A preparation whose race is run or abandoned gives way to a recovery; one whose race is
  cancelled or dropped, to the general preparation.
- The general preparation gives way to a recovery when a race is run after it was
  written, and to its next block on the last day of its block.
- A recovery gives way to the general preparation on its last day; so does a rebuild,
  or as soon as the volume held is run again.

A transition starts with the week after `today`: on a cycle's last day, the Sunday its
review is written, the next cycle follows its last week. Entering a preparation is never
automatic: a plan review proposes it when a candidate can start (`review_week`), the
athlete takes it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from . import iso_week
from . import plan_generator as generator
from .cycle_templates import CycleTemplates
from .cycles import CycleVersion, ends_on
from .efforts import Break
from .errors import ConfigError
from .plan_generator import Inputs, Trame
from .race import RECOVERED_FROM, Race
from .vocabulary import CycleCause, CycleKind, GeneralLevel, RaceState

#: The race states that end a preparation without a race run.
LEFT_UNRUN = frozenset({RaceState.CANCELLED, RaceState.DROPPED})


@dataclass(frozen=True)
class Standing:
    """Where the athlete stands, over the closed weeks the start reads.

    `run_km`: the mean weekly running volume, assimilation weeks left out. `start_km`: the
    same, the weeks a cycle planned low (recovery, rebuild) left out too, None when every
    one is. `floor_km`: the rebuild's floor."""

    run_km: float
    start_km: float | None
    floor_km: float

    @property
    def under_floor(self) -> bool:
        return self.start_km is not None and self.start_km < self.floor_km


@dataclass(frozen=True)
class Transition:
    kind: CycleKind
    cause: CycleCause
    level: GeneralLevel | None = None
    #: The race a recovery follows.
    race: Race | None = None


def _recovery_weeks(templates: CycleTemplates, race: Race) -> int | None:
    if race.distance_km is None:
        return None
    template = templates.recovery_after(race.distance_km)
    return None if template is None else int(template.weeks.low) or 1


def _recovering_from(races: Sequence[Race], templates: CycleTemplates, today: dt.date,
                     since: dt.date | None = None) -> Race | None:
    """The last race run, on `since` or after it, whose recovery is not over."""
    for race in reversed(races):
        if race.state not in RECOVERED_FROM or race.on > today:
            continue
        if since is not None and race.on < since:
            return None
        weeks = _recovery_weeks(templates, race)
        if weeks is not None and today <= race.on + dt.timedelta(weeks=weeks):
            return race
        return None
    return None


def _general(templates: CycleTemplates, cause: CycleCause = CycleCause.TRANSITION,
             level: GeneralLevel | None = None) -> Transition:
    return Transition(CycleKind.GENERAL, cause, level=level or templates.default_level)


def next_cycle(active: CycleVersion | None, races: Sequence[Race], breaks: Sequence[Break],
               standing: Standing, today: dt.date, templates: CycleTemplates, *,
               first: bool = False) -> Transition | None:
    """The transition due today, or None: the active cycle goes on. `first`: the instance
    has no version yet, so the one written is its takeover."""
    if active is None:
        cause = CycleCause.MIGRATION if first else CycleCause.TRANSITION
        ran = _recovering_from(races, templates, today)
        if ran is not None:
            return Transition(CycleKind.RECOVERY, cause, race=ran)
        return _general(templates, cause)
    broken = any(pause.starts_on <= today <= pause.ends_on for pause in breaks)
    last_day = today >= ends_on(active.trame)
    if active.kind == CycleKind.REBUILD:
        back = standing.run_km >= active.trame.hypotheses.held_km
        if not broken and (last_day or back):
            return _general(templates)
        return None
    if broken or (standing.under_floor and active.kind != CycleKind.RECOVERY):
        return Transition(CycleKind.REBUILD, CycleCause.TRANSITION)
    if active.kind == CycleKind.PREPARATION:
        race = next((race for race in races if race.key == active.race_key), None)
        if race is not None and race.state in RECOVERED_FROM:
            return Transition(CycleKind.RECOVERY, CycleCause.TRANSITION, race=race)
        if race is None or race.state in LEFT_UNRUN:
            return _general(templates)
        return None
    if active.kind == CycleKind.GENERAL:
        ran = (_recovering_from(races, templates, today, active.created_on)
               if active.created_on is not None else None)
        if ran is not None:
            return Transition(CycleKind.RECOVERY, CycleCause.TRANSITION, race=ran)
        if last_day:
            return _general(templates, CycleCause.BLOCK_END, active.level)
        return None
    return _general(templates) if last_day else None


def review_week(active: CycleVersion | None, goal: Race | None, templates: CycleTemplates,
                today: dt.date, *, proposal_open: bool, reviewed: bool) -> str | None:
    """The week a plan review is due for, or None. It is due when a candidate of the goal's
    preparation can start the week after today, while no preparation is in force nor
    proposed: on the first week a candidate starts, or at once for a goal whose window was
    already open when no review had been asked yet (`reviewed`)."""
    if (goal is None or goal.goal is None or goal.on < today or proposal_open
            or (active is not None and active.kind == CycleKind.PREPARATION)):
        return None
    template = templates.preparation_for(goal.goal.distance_km, goal.goal.terrain)
    if template is None:
        return None
    upcoming = iso_week.shift(iso_week.of(today), 1)
    starts = set(generator.candidate_starts(template, goal.on).values())
    if upcoming < min(starts) or upcoming > iso_week.of(goal.on):
        return None
    return upcoming if upcoming in starts or not reviewed else None


def trame(transition: Transition, templates: CycleTemplates, inputs: Inputs) -> Trame:
    """The trame the transition starts: the general block at its level, the recovery of the
    race's distance, or the rebuild."""
    if transition.kind == CycleKind.GENERAL:
        level = transition.level or templates.default_level
        general = templates.general_at(level)
        if general is None:
            raise ConfigError(f"no general template at the level {level}")
        return generator.general(general, inputs)
    if transition.kind == CycleKind.RECOVERY:
        race = transition.race
        distance = race.distance_km if race is not None else None
        recovery = templates.recovery_after(distance) if distance is not None else None
        if recovery is None:
            raise ConfigError("no recovery template serves the race's distance")
        return generator.recovery(recovery, inputs)
    if transition.kind == CycleKind.REBUILD:
        return generator.rebuild(templates.rebuild, inputs)
    raise ValueError(f"a {transition.kind} is never entered by a transition")
