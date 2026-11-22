"""A plan skeleton, built backwards from the race: the engine is the only author of weeks.

From the race's goal (`race.Goal`), its template (`plan_templates`), where the athlete
stands (the weekly volume and the longest run of late) and the levers of a candidate, the
skeleton lays out every week from the next one to the race week: its phase, whether it
is a deload, the volume it may reach, the long run it may reach, the quality sessions it
may carry and their families, and the default climb intent.

How it is laid out:
- the taper last (`taper_weeks`, the race week included), a recovery first after a
  recent race, then the specific block (a lever), development, and base absorbing what
  remains; short of weeks, development then the specific block shrink to their minimum
  before base goes below its own, which is then a risk;
- deloads every `deload_every` weeks, counted backwards from the specific block so one
  precedes it, never the week before the taper; the rulebook's assimilation weeks too;
- volume and long run climb linearly from where the athlete stands to the goal's peak,
  reached the week before the taper, each week at most `ramp_max_pct` and
  `long_step_max_pct` above the last build week: lost weeks are never caught up, the
  peak is then out of reach, and that is a risk to read, not a ramp to steepen;
- a deload cuts the last build week by `deload_cut_pct`, the taper by the race's taper
  band.

Re-planning is the same computation run again from what was done: the skeleton always
starts from the athlete's actual state and the levers chosen, so a week off compresses
or lengthens what is left, it is never made up.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

from . import iso_week
from .plan_templates import Template
from .race import Goal
from .vocabulary import ClimbIntent, PlanPhase, RaceTerrain


class SkeletonRisk(StrEnum):
    """What a candidate asks that deserves to be read before it is chosen."""

    #: Base is shorter than the template's minimum: too few weeks to the race.
    BASE_SHORT = "base_short"
    #: The weekly volume cannot reach its peak within the ramp.
    PEAK_VOLUME_UNREACHED = "peak_volume_unreached"
    #: The long run cannot reach its peak within the step.
    PEAK_LONG_RUN_UNREACHED = "peak_long_run_unreached"
    #: The specific block is shorter than the lever asked: too few weeks.
    SPECIFIC_SHORT = "specific_short"


@dataclass(frozen=True)
class Levers:
    """What varies between candidates."""

    specific_weeks: int
    deload_every: int
    second_quality: PlanPhase


@dataclass(frozen=True)
class Start:
    """Where the athlete stands: the weekly running volume and the longest run of late."""

    volume_km: float
    long_run_km: float


@dataclass(frozen=True)
class Limits:
    """How fast the volume and the long run may climb from one build week to the next."""

    ramp_max_pct: float
    long_step_max_pct: float
    taper_cut_pct: float
    quality_per_week: int | None
    assimilation: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Week:
    label: str
    phase: PlanPhase
    deload: bool
    volume_km: float
    #: The long run the week may reach; 0 in race week.
    long_run_km: float
    qualities: int
    families: tuple[str, ...]
    climb_intent: ClimbIntent
    power_hike_from_pct: int | None = None


@dataclass(frozen=True)
class Risk:
    kind: SkeletonRisk
    value: float
    reference: float


@dataclass(frozen=True)
class Skeleton:
    candidate: str
    levers: Levers
    template: str
    weeks: tuple[Week, ...]
    risks: tuple[Risk, ...]

    def week(self, label: str) -> Week | None:
        return next((week for week in self.weeks if week.label == label), None)


def _phases(template: Template, levers: Levers, count: int,
            recovering: bool) -> tuple[list[PlanPhase], list[Risk]]:
    taper = min(template.taper_weeks, count)
    recovery = min(template.phase(PlanPhase.RECOVERY).minimum if recovering else 0,
                   count - taper)
    left = count - taper - recovery
    specific_range = template.phase(PlanPhase.SPECIFIC)
    development_range = template.phase(PlanPhase.DEVELOPMENT)
    base_range = template.phase(PlanPhase.BASE)
    wanted = min(max(levers.specific_weeks, specific_range.minimum), specific_range.maximum)
    specific = min(wanted, left)
    development = min(development_range.maximum, left - specific)
    base = left - specific - development
    for phase_range, name in ((development_range, "development"),
                              (specific_range, "specific")):
        need = base_range.minimum - base
        if need <= 0:
            break
        current = development if name == "development" else specific
        take = min(need, max(current - phase_range.minimum, 0))
        if name == "development":
            development -= take
        else:
            specific -= take
        base += take
    risks = []
    if base < base_range.minimum:
        risks.append(Risk(SkeletonRisk.BASE_SHORT, float(base), float(base_range.minimum)))
    if specific < wanted:
        risks.append(Risk(SkeletonRisk.SPECIFIC_SHORT, float(specific), float(wanted)))
    order = ([PlanPhase.RECOVERY] * recovery + [PlanPhase.BASE] * base
             + [PlanPhase.DEVELOPMENT] * development + [PlanPhase.SPECIFIC] * specific
             + [PlanPhase.TAPER] * taper)
    return order, risks


def _deloads(phases: list[PlanPhase], every: int, labels: list[str],
             assimilation: frozenset[str]) -> set[int]:
    build = [index for index, phase in enumerate(phases)
             if phase in (PlanPhase.BASE, PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC)]
    if not build:
        return set()
    first, last = build[0], build[-1]
    specific = next((index for index, phase in enumerate(phases)
                     if phase == PlanPhase.SPECIFIC), last + 1)
    found = set()
    index = specific - 1
    while index >= first + every - 1:
        found.add(index)
        index -= every
    index = specific - 1 + every
    while index < last:
        found.add(index)
        index += every
    found |= {index for index in build if labels[index] in assimilation and index != last}
    return found


def _climb(goal: Goal, phase: PlanPhase) -> tuple[ClimbIntent, int | None]:
    if phase == PlanPhase.RECOVERY:
        return ClimbIntent.EASY, None
    if goal.terrain == RaceTerrain.TRAIL:
        if goal.power_hike_from_pct is not None:
            return ClimbIntent.POWER_HIKE, goal.power_hike_from_pct
        return ClimbIntent.EASY, None
    return ClimbIntent.RUN_SLOW, None


def generate(template: Template, goal: Goal, levers: Levers, start: Start, limits: Limits,
             first: dt.date, race_on: dt.date, *, recovering: bool = False,
             candidate: str = "") -> Skeleton:
    """The skeleton from the week of `first` to the race week."""
    if goal.peak_volume_km is None or goal.peak_long_run_km is None:
        raise ValueError("a skeleton needs the goal's peak volume and peak long run")
    labels = []
    week = iso_week.of(first)
    while week <= iso_week.of(race_on):
        labels.append(week)
        week = iso_week.shift(week, 1)
    phases, risks = _phases(template, levers, len(labels), recovering)
    deloads = _deloads(phases, levers.deload_every, labels, limits.assimilation)
    builds = [index for index, phase in enumerate(phases)
              if phase in (PlanPhase.BASE, PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC)
              and index not in deloads]
    cut = template.deload_cut_pct / 100
    taper_cut = limits.taper_cut_pct / 100
    volume, long_run = start.volume_km, start.long_run_km
    peak_volume, peak_long = start.volume_km, start.long_run_km
    weeks: list[Week] = []
    previous_qualities = 0
    for index, (label, phase) in enumerate(zip(labels, phases, strict=True)):
        deload = index in deloads
        if phase == PlanPhase.RECOVERY:
            week_volume, week_long = start.volume_km * (1 - cut), start.long_run_km * (1 - cut)
        elif phase == PlanPhase.TAPER:
            week_volume = peak_volume * (1 - taper_cut)
            week_long = 0.0 if label == labels[-1] else peak_long * (1 - taper_cut)
        elif deload:
            week_volume, week_long = volume * (1 - cut), long_run * (1 - cut)
        else:
            step = builds.index(index) + 1
            share = step / len(builds)
            target_volume = start.volume_km + (goal.peak_volume_km - start.volume_km) * share
            target_long = (start.long_run_km
                           + (goal.peak_long_run_km - start.long_run_km) * share)
            volume = max(min(target_volume, volume * (1 + limits.ramp_max_pct / 100)), volume)
            long_run = max(min(target_long, long_run * (1 + limits.long_step_max_pct / 100)),
                           long_run)
            peak_volume, peak_long = volume, long_run
            week_volume, week_long = volume, long_run
        phase_spec = template.phase(phase)
        qualities = phase_spec.qualities
        if limits.quality_per_week is not None:
            qualities = min(qualities, limits.quality_per_week)
        if levers.second_quality == PlanPhase.SPECIFIC and phase in (
                PlanPhase.BASE, PlanPhase.DEVELOPMENT):
            qualities = min(qualities, 1)
        if deload:
            qualities = min(qualities, previous_qualities)
        previous_qualities = qualities
        families = tuple(family for family in phase_spec.families
                         if goal.families is None or family in goal.families)
        intent, grade = _climb(goal, phase)
        weeks.append(Week(label, phase, deload, round(week_volume, 1), round(week_long, 1),
                          qualities if families else 0, families, intent, grade))
    if builds and peak_volume < goal.peak_volume_km - 0.05:
        risks.append(Risk(SkeletonRisk.PEAK_VOLUME_UNREACHED, round(peak_volume, 1),
                          goal.peak_volume_km))
    if builds and peak_long < goal.peak_long_run_km - 0.05:
        risks.append(Risk(SkeletonRisk.PEAK_LONG_RUN_UNREACHED, round(peak_long, 1),
                          goal.peak_long_run_km))
    return Skeleton(candidate, levers, template.slug, tuple(weeks), tuple(risks))


def candidate_levers(template: Template) -> dict[str, Levers]:
    """Two or three distinct sets of levers: the specific block's length, the deload
    rhythm and when the second quality session arrives."""
    specific = template.phase(PlanPhase.SPECIFIC)
    low, high = specific.minimum, specific.maximum
    tight, loose = min(template.deload_every), max(template.deload_every)
    options = {
        "cautious": Levers(low, tight, PlanPhase.SPECIFIC),
        "balanced": Levers((low + high + 1) // 2, loose, PlanPhase.SPECIFIC),
        "specific": Levers(high, loose, PlanPhase.DEVELOPMENT),
    }
    unique: dict[str, Levers] = {}
    for slug, levers in options.items():
        if levers not in unique.values():
            unique[slug] = levers
    return unique


def candidates(template: Template, goal: Goal, start: Start, limits: Limits,
               first: dt.date, race_on: dt.date, *, recovering: bool = False) -> list[Skeleton]:
    """The engine's candidates for the race: cautious, balanced, specific."""
    return [generate(template, goal, levers, start, limits, first, race_on,
                     recovering=recovering, candidate=slug)
            for slug, levers in candidate_levers(template).items()]
