"""The trame of a training cycle, generated whole from frozen inputs: the engine writes
every week once.

A trame lays out each week of a cycle: its phase, whether it is a deload, the volume and
the long run it may reach, its quality sessions, and its key sessions with their dose and
intensity. It is generated from arguments only, never the store or the clock: the same
inputs give the same trame, and a trame is never computed again. A new one comes from new
inputs, which the version that keeps it records (`Hypotheses`).

A preparation is built backwards from the race, over its window: development, specific
block, taper (the weeks before the window are the general preparation):
- the phases take the template's lengths, the specific block the candidate's lever;
  short of weeks, development then the specific block shrink to their minimum, and below
  it the window is short, a risk;
- deloads come every `deload_every` weeks, counted backwards from the specific block so
  one precedes it, never the week before the taper; placed once, they never drift;
- the volume climbs from where the athlete stands to the proposed peak, at most
  `ramp_max_pct` a build week; a week that adds a quality session holds the volume, and a
  deload cuts the last build week by the template's cut;
- the proposed peak is the lowest of what the ramp reaches, what the athlete's available
  time holds at their easy pace, and the template's share of the highest volume held: a
  lower peak may be chosen, never a higher one;
- the taper cuts progressively, from the bottom of its band to the top, so its mean drop
  stays within the band;
- each phase's key sessions alternate when a week carries fewer quality sessions than the
  phase has; a key session's dose moves from its start (the lineage's position for the
  first phase it appears in, else the template's) towards its arrival, one variable by
  one step per exposure, never in a deload nor in a week whose volume rises, and never
  beyond the larger of the two doses' work; an arrival out of reach is a risk, not a
  steeper slope; the last exposures named `flagship` are the block's flagship sessions;
- a test opens the window when the calibration is missing, provisional or stale;
- no week carries more quality sessions than the rulebook allows (rule n1).

The general preparation is a block of `block_weeks` within its level's volume band, one
key session a week; its deloads are triggered by signals, outside the trame. A recovery
comes back within its band after a race; a rebuild climbs from its floor to the volume
held.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from . import iso_week
from .cycle_templates import (
    General,
    Intensity,
    KeySession,
    Preparation,
    Range,
    Rebuild,
    Recovery,
)
from .progression import Dose
from .vocabulary import CycleKind, KeyRole, PlanPhase, RaceTerrain

#: A trame's figures are rounded to 0.1 km.
ROUND_KM = 1
#: The phases whose weeks build.
BUILD_PHASES = (PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC)
#: The longest rebuild a trame lays out, in weeks.
REBUILD_WEEKS_MAX = 16


class TrameRisk(StrEnum):
    """What a trame asks that deserves to be read before it is taken."""

    #: Fewer weeks before the race than the phases' minimum.
    WINDOW_SHORT = "window_short"
    #: The specific block is shorter than the candidate's lever.
    SPECIFIC_SHORT = "specific_short"
    #: The athlete's available time caps the peak below what the ramp would reach.
    TIME_BOUND = "time_bound"
    #: A key session cannot reach its arrival at one step per exposure.
    ARRIVAL_UNREACHED = "arrival_unreached"
    #: The highest volume held is under the template's domain.
    BELOW_DOMAIN = "below_domain"


@dataclass(frozen=True)
class Risk:
    kind: TrameRisk
    value: float
    reference: float
    #: The key session's workout and phase, for a risk of one key session.
    workout: str = ""
    phase: PlanPhase | None = None


@dataclass(frozen=True)
class Levers:
    """What varies between a preparation's candidates."""

    specific_weeks: int
    deload_every: int
    #: The phase from which a week may carry its second quality session.
    second_quality: PlanPhase
    #: A peak chosen under the proposed one; None: the proposed peak.
    peak_km: float | None = None


@dataclass(frozen=True)
class Inputs:
    """Everything a trame is generated from, frozen with the version that keeps it."""

    #: The first week of the cycle.
    origin: str
    #: The weekly volume and the longest run the athlete starts from.
    start_km: float
    start_long_km: float
    #: The highest weekly volume the athlete held.
    held_km: float
    #: How fast the volume and the long run may climb from one build week to the next.
    ramp_max_pct: float
    long_step_max_pct: float
    #: Minutes the athlete may run in a week; None when a day sets no limit.
    minutes_per_week: int | None = None
    #: The athlete's easy pace, in seconds per kilometre; None without easy runs.
    easy_pace_s: float | None = None
    #: The calibration in force is measured and recent: no test opens the cycle.
    calibrated: bool = False
    #: The day of the calibration in force, None without one.
    calibration_on: dt.date | None = None
    #: The dose each run lineage stands at, by the workout it follows.
    lineages: tuple[tuple[str, Dose], ...] = ()
    #: The most quality sessions a week the rulebook allows (rule n1); None: the template's.
    qualities_max: int | None = None

    def lineage(self, workout: str) -> Dose | None:
        return dict(self.lineages).get(workout)


@dataclass(frozen=True)
class Goal:
    """The race a preparation is for. Its terrain and goal time shape no week: they are
    frozen with the version for its hypotheses (`redline.model.hypotheses`). A terrain is
    None in a version written before it was frozen."""

    on: dt.date
    distance_km: float
    terrain: RaceTerrain | None = None
    goal_time_s: int | None = None


@dataclass(frozen=True)
class Segment:
    """The part of a long run run at an intensity."""

    km: float
    intensity: Intensity


@dataclass(frozen=True)
class Week:
    label: str
    phase: PlanPhase
    deload: bool
    volume_km: float
    #: The long run the week may reach; 0 in race week.
    long_run_km: float
    qualities: int
    segment: Segment | None = None


@dataclass(frozen=True)
class Key:
    """A key session of the trame, in its week."""

    week: str
    position: int
    workout: str
    role: KeyRole
    #: None for a test, whose protocol is its own.
    dose: Dose | None = None
    intensity: Intensity | None = None


@dataclass(frozen=True)
class Trame:
    kind: CycleKind
    #: The template's slug, or the general level.
    template: str
    candidate: str
    weeks: tuple[Week, ...]
    keys: tuple[Key, ...]
    risks: tuple[Risk, ...]
    #: The inputs the trame holds as long as they do.
    hypotheses: Inputs
    levers: Levers | None = None
    goal: Goal | None = None

    def week(self, label: str) -> Week | None:
        return next((week for week in self.weeks if week.label == label), None)


def _km(value: float) -> float:
    return round(value, ROUND_KM)


def _labels(origin: str, count: int) -> list[str]:
    return [iso_week.shift(origin, offset) for offset in range(count)]


# ------------------------------------------------------------------ doses


def work(dose: Dose) -> float:
    return (dose.get("reps") or 1) * (dose.get("rep_seconds") or 0)


def _towards(current: float, target: float, step: float) -> float:
    return min(current + step, target) if target > current else max(current - step, target)


def next_dose(dose: Dose, key: KeySession) -> Dose:
    """One variable by one step towards the arrival: the one with the most steps left
    whose move keeps the work within the larger of the start's and the arrival's."""
    ceiling = max(work(key.start), work(key.arrival), work(dose))
    moves = []
    for name, value in dose.values:
        target, step = key.arrival.get(name), key.step.get(name)
        if target is None or step is None or value == target:
            continue
        moved = dose.with_value(name, _towards(value, target, step))
        if work(moved) <= ceiling:
            moves.append((-abs(target - value) / step, moved))
    return min(moves, key=lambda move: move[0])[1] if moves else dose


def _steps_left(dose: Dose, key: KeySession) -> float:
    return sum(abs((key.arrival.get(name) or value) - value) / (key.step.get(name) or 1)
               for name, value in dose.values)


# ------------------------------------------------------------------ preparation


def window_weeks(template: Preparation, levers: Levers, count: int) -> tuple[list[PlanPhase],
                                                                             list[Risk]]:
    """The phase of each week of a window of `count` weeks, the taper last."""
    taper = min(template.taper_weeks, count)
    development_spec = template.phase(PlanPhase.DEVELOPMENT)
    specific_spec = template.phase(PlanPhase.SPECIFIC)
    spec_low = int(specific_spec.weeks.low) if specific_spec else 0
    spec_high = int(specific_spec.weeks.high) if specific_spec else 0
    dev_low = int(development_spec.weeks.low) if development_spec else 0
    dev_high = int(development_spec.weeks.high) if development_spec else 0
    left = count - taper
    wanted = min(max(levers.specific_weeks, spec_low), spec_high)
    specific = min(wanted, left)
    development = min(dev_high, left - specific)
    if development < dev_low and specific > spec_low:
        moved = min(dev_low - development, specific - spec_low)
        specific -= moved
        development += moved
    risks = []
    if count < taper + dev_low + spec_low:
        risks.append(Risk(TrameRisk.WINDOW_SHORT, float(count),
                          float(template.taper_weeks + dev_low + spec_low)))
    if specific < wanted:
        risks.append(Risk(TrameRisk.SPECIFIC_SHORT, float(specific), float(wanted)))
    phases = ([PlanPhase.DEVELOPMENT] * development + [PlanPhase.SPECIFIC] * specific
              + [PlanPhase.TAPER] * taper)
    return phases, risks


def _deloads(phases: Sequence[PlanPhase], every: int) -> set[int]:
    builds = [index for index, phase in enumerate(phases) if phase in BUILD_PHASES]
    if not builds:
        return set()
    first, last = builds[0], builds[-1]
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
    return found


def _peak(template: Preparation, inputs: Inputs, builds: int) -> tuple[float, list[Risk]]:
    """The proposed peak: the lowest of the ramp's reach, the time cap and the template's
    share of the volume held."""
    reach = inputs.start_km * (1 + inputs.ramp_max_pct / 100) ** builds
    held = template.peak_pct_of_held / 100 * max(inputs.held_km, inputs.start_km)
    risks = []
    peak = min(reach, held)
    if inputs.minutes_per_week is not None and inputs.easy_pace_s:
        time = inputs.minutes_per_week * 60 / inputs.easy_pace_s
        if time < peak:
            risks.append(Risk(TrameRisk.TIME_BOUND, _km(time), _km(peak)))
            peak = time
    return max(peak, inputs.start_km), risks


def _taper_cuts(template: Preparation) -> list[float]:
    """Each taper week's cut, from the bottom of the band to its top."""
    low, high, weeks = template.taper_cut_pct.low, template.taper_cut_pct.high, \
        template.taper_weeks
    if weeks == 1:
        return [(low + high) / 2 / 100]
    return [(low + (high - low) * index / (weeks - 1)) / 100 for index in range(weeks)]


def _qualities(template: Preparation, levers: Levers, phase: PlanPhase) -> int:
    spec = template.phase(phase)
    qualities = spec.qualities if spec else 0
    if (levers.second_quality == PlanPhase.SPECIFIC and phase == PlanPhase.DEVELOPMENT):
        qualities = min(qualities, 1)
    return qualities


@dataclass
class _Lineage:
    """A key session's dose, carried from one exposure to the next within a phase."""

    key: KeySession
    dose: Dose
    exposures: int = 0


def _keys(template: Preparation, weeks: Sequence[Week], inputs: Inputs,
          progress: Mapping[str, bool]) -> tuple[list[Key], list[Risk]]:
    keys: list[Key] = []
    risks: list[Risk] = []
    seen: set[str] = set()
    deloads = {week.label: week.deload for week in weeks}
    for spec in template.phases:
        phase_weeks = [week for week in weeks if week.phase == spec.name]
        if not spec.keys or not phase_weeks:
            continue
        doses = {}
        for key in spec.keys:
            start = inputs.lineage(key.workout) if key.workout not in seen else None
            doses[key.workout] = _Lineage(key, start or key.start)
            seen.add(key.workout)
        exposures: dict[str, list[int]] = {key.workout: [] for key in spec.keys}
        turn = 0
        for week in phase_weeks:
            count = min(week.qualities, len(spec.keys))
            for position in range(count):
                key = spec.keys[(turn + position) % len(spec.keys)]
                state = doses[key.workout]
                if state.exposures and progress[week.label]:
                    state.dose = next_dose(state.dose, key)
                state.exposures += 1
                exposures[key.workout].append(len(keys))
                keys.append(Key(week.label, position + 1, key.workout, KeyRole.KEY, state.dose,
                                key.intensity))
            turn += max(count, 1)
        for key in spec.keys:
            state = doses[key.workout]
            left = _steps_left(state.dose, key)
            if left > 0:
                risks.append(Risk(TrameRisk.ARRIVAL_UNREACHED, round(left, 1), 0.0, key.workout,
                                  spec.name))
            loaded = [index for index in exposures[key.workout]
                      if not deloads.get(keys[index].week, False)]
            for index in loaded[-key.flagship:] if key.flagship else []:
                keys[index] = replace(keys[index], role=KeyRole.FLAGSHIP)
    return keys, risks


def _longest(template: Preparation, levers: Levers) -> int:
    """The longest window the candidate's levers give: the taper, the specific block they
    ask, the longest development."""
    development = template.phase(PlanPhase.DEVELOPMENT)
    specific = template.phase(PlanPhase.SPECIFIC)
    wanted = (min(max(levers.specific_weeks, int(specific.weeks.low)), int(specific.weeks.high))
              if specific else 0)
    return template.taper_weeks + wanted + (int(development.weeks.high) if development else 0)


class _Turn(StrEnum):
    """What a week of the window changes: one thing at a time, so its answer can be read."""

    #: The first build week: where the athlete stands.
    START = "start"
    #: The volume and the long run climb; the doses hold.
    VOLUME = "volume"
    #: The doses climb; the volume holds.
    DOSES = "doses"
    #: A quality session is added; the volume and the doses hold.
    QUALITY = "quality"
    DELOAD = "deload"
    TAPER = "taper"


def _turns(phases: Sequence[PlanPhase], deloads: set[int],
           qualities: Sequence[int]) -> list[_Turn]:
    """Each week's turn: build weeks alternate between the volume and the doses."""
    turns: list[_Turn] = []
    held, volume_next = 0, True
    for index, phase in enumerate(phases):
        if phase == PlanPhase.TAPER:
            turns.append(_Turn.TAPER)
        elif index in deloads:
            turns.append(_Turn.DELOAD)
        elif _Turn.START not in turns:
            turns.append(_Turn.START)
            held = qualities[index]
        elif qualities[index] > held:
            turns.append(_Turn.QUALITY)
            held = qualities[index]
        else:
            turns.append(_Turn.VOLUME if volume_next else _Turn.DOSES)
            volume_next = not volume_next
    return turns


def preparation(template: Preparation, goal: Goal, levers: Levers, inputs: Inputs, *,
                candidate: str = "") -> Trame:
    """The trame of a preparation to the race week, from the window's first week, never
    before `inputs.origin`: the weeks before the window are the general preparation."""
    race_week = iso_week.of(goal.on)
    count = min(_distance(inputs.origin, race_week) + 1, _longest(template, levers))
    labels = _labels(iso_week.shift(race_week, 1 - count), count)
    phases, risks = window_weeks(template, levers, count)
    deloads = _deloads(phases, levers.deload_every)
    qualities: list[int] = []
    for index, phase in enumerate(phases):
        quality = _qualities(template, levers, phase)
        if inputs.qualities_max is not None:
            quality = min(quality, inputs.qualities_max)
        qualities.append(min(quality, qualities[-1]) if index in deloads and qualities
                         else quality)
    turns = _turns(phases, deloads, qualities)
    climbs = turns.count(_Turn.VOLUME)
    peak, peak_risks = _peak(template, inputs, climbs)
    if levers.peak_km is not None:
        peak = max(min(peak, levers.peak_km), inputs.start_km)
    risks += peak_risks
    if inputs.held_km < template.min_held_km:
        risks.append(Risk(TrameRisk.BELOW_DOMAIN, inputs.held_km, template.min_held_km))
    long_peak = min(template.long_run_km_max, peak * max(
        (spec.long_run_share_max or 0) for spec in template.phases))
    cut = (template.deload.cut_pct or 0) / 100
    tapers = iter(_taper_cuts(template))
    volume, long_run = inputs.start_km, inputs.start_long_km
    built_long, climbed = long_run, 0
    weeks: list[Week] = []
    for index, (label, phase, turn) in enumerate(zip(labels, phases, turns, strict=True)):
        spec = template.phase(phase)
        share = spec.long_run_share_max if spec and spec.long_run_share_max else None
        if turn == _Turn.VOLUME:
            climbed += 1
            way = climbed / climbs
            volume = max(min(inputs.start_km + (peak - inputs.start_km) * way,
                             volume * (1 + inputs.ramp_max_pct / 100)), volume)
            long_run = max(min(inputs.start_long_km + (long_peak - inputs.start_long_km) * way,
                               long_run * (1 + inputs.long_step_max_pct / 100)), long_run)
        if turn == _Turn.TAPER:
            drop = next(tapers)
            week_volume = volume * (1 - drop)
            week_long = 0.0 if label == labels[-1] else built_long * (1 - drop)
        elif turn == _Turn.DELOAD:
            week_volume, week_long = volume * (1 - cut), built_long * (1 - cut)
        else:
            week_volume = volume
            # The long run climbs from the one the athlete last ran, never from a value its
            # phase's share held back.
            week_long = min(long_run, template.long_run_km_max,
                            volume * share if share else long_run)
            long_run = built_long = week_long
        segment = None
        if spec and spec.segments and turn != _Turn.DELOAD and week_long:
            own = [week for week in range(len(phases)) if phases[week] == phase]
            position = own.index(index) / max(len(own) - 1, 1)
            share_low, share_high = spec.segments.share.low, spec.segments.share.high
            segment = Segment(_km(week_long * (share_low + (share_high - share_low)
                                              * position)), spec.segments.intensity)
        weeks.append(Week(label, phase, turn == _Turn.DELOAD, _km(week_volume), _km(week_long),
                          qualities[index], segment))
    progress = {label: turn in (_Turn.DOSES, _Turn.TAPER)
                for label, turn in zip(labels, turns, strict=True)}
    keys, key_risks = _keys(template, weeks, inputs, progress)
    if not inputs.calibrated and weeks:
        keys = [Key(weeks[0].label, 0, "test", KeyRole.TEST),
                *(key for key in keys if not (key.week == weeks[0].label and key.position == 1))]
    return Trame(CycleKind.PREPARATION, template.slug, candidate, tuple(weeks), tuple(keys),
                 tuple(risks + key_risks), inputs, levers, goal)


def _distance(start: str, end: str) -> int:
    return (iso_week.monday(end) - iso_week.monday(start)).days // 7


def candidate_levers(template: Preparation) -> dict[str, Levers]:
    """Two or three distinct sets of levers: the specific block's length, the deload rhythm,
    when the second quality session arrives."""
    specific = template.phase(PlanPhase.SPECIFIC)
    low = int(specific.weeks.low) if specific else 0
    high = int(specific.weeks.high) if specific else 0
    every = template.deload.every_weeks or Range(4, 4)
    tight, loose = int(every.low), int(every.high)
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


def candidate_starts(template: Preparation, race_on: dt.date) -> dict[str, str]:
    """The first week of each candidate's window, by candidate: from it, the candidate can
    be proposed."""
    race_week = iso_week.of(race_on)
    return {slug: iso_week.shift(race_week, 1 - _longest(template, levers))
            for slug, levers in candidate_levers(template).items()}


def window_start(template: Preparation, race_on: dt.date) -> str:
    """The first week of the longest window the candidates may ask: from it, the
    preparation can be taken."""
    return min(candidate_starts(template, race_on).values())


def candidates(template: Preparation, goal: Goal, inputs: Inputs) -> list[Trame]:
    """The engine's candidates for the race: cautious, balanced, specific."""
    return [preparation(template, goal, levers, inputs, candidate=slug)
            for slug, levers in candidate_levers(template).items()]


# ------------------------------------------------------------------ the other cycles


def _band(template_pct: Range, held_km: float, band_from_km: float | None = None) -> Range:
    if band_from_km is not None and held_km < band_from_km:
        return Range(held_km, held_km)
    return Range(held_km * template_pct.low / 100, held_km * template_pct.high / 100)


def general(template: General, inputs: Inputs) -> Trame:
    """A block of the general preparation at the template's level."""
    held = max(inputs.held_km, inputs.start_km)
    band = _band(template.volume_pct, held, template.band_from_km)
    ramp = template.ramp_max_pct if template.ramp_max_pct is not None else inputs.ramp_max_pct
    volume = inputs.start_km
    weeks, keys = [], []
    dose = {key.workout: inputs.lineage(key.workout) or key.start for key in template.keys}
    qualities = (template.qualities if inputs.qualities_max is None
                 else min(template.qualities, inputs.qualities_max))
    for index, label in enumerate(_labels(inputs.origin, template.block_weeks)):
        # The progress level climbs to the top of its band; the others hold within it,
        # climbing to its bottom at the ramp, coming down to its top at once.
        target = (band.high if template.ramp_max_pct is not None
                  else min(max(volume, band.low), band.high))
        volume = min(target, volume * (1 + ramp / 100)) if target > volume else target
        long_run = min(inputs.start_long_km * (1 + inputs.long_step_max_pct / 100) ** index,
                       volume * template.long_run_share_max)
        weeks.append(Week(label, PlanPhase.GENERAL, False, _km(volume), _km(long_run),
                          qualities))
        for position, key in enumerate(template.keys[:qualities], start=1):
            if index:
                dose[key.workout] = next_dose(dose[key.workout], key)
            keys.append(Key(label, position, key.workout, KeyRole.KEY, dose[key.workout],
                            key.intensity))
    return Trame(CycleKind.GENERAL, str(template.level), "", tuple(weeks), tuple(keys), (),
                 inputs)


def recovery(template: Recovery, inputs: Inputs) -> Trame:
    """The weeks after a race: no quality session, a volume back within its band."""
    held = max(inputs.held_km, inputs.start_km)
    count = int(template.weeks.low) or 1
    weeks = []
    for index, label in enumerate(_labels(inputs.origin, count)):
        position = index / max(count - 1, 1)
        pct = template.volume_pct.low + (template.volume_pct.high - template.volume_pct.low) \
            * position
        volume = held * pct / 100
        weeks.append(Week(label, PlanPhase.RECOVERY, False, _km(volume),
                          _km(volume * template.long_run_share_max), 0))
    return Trame(CycleKind.RECOVERY, f"recovery-{template.distance_km_max:g}", "",
                 tuple(weeks), (), (), inputs)


def rebuild(template: Rebuild, inputs: Inputs) -> Trame:
    """The way back after a break: from the floor to the volume held, at the ramp."""
    held = max(inputs.held_km, inputs.start_km)
    volume = max(inputs.start_km, held * template.floor_pct / 100)
    weeks = []
    for label in _labels(inputs.origin, REBUILD_WEEKS_MAX):
        weeks.append(Week(label, PlanPhase.REBUILD, False, _km(volume),
                          _km(volume * template.long_run_share_max), 0))
        if volume >= held:
            break
        volume = min(held, volume * (1 + template.ramp_max_pct / 100))
    return Trame(CycleKind.REBUILD, "rebuild", "", tuple(weeks), (), (), inputs)
