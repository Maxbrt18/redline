"""Whether a preparation's version still holds: its hypotheses against the real state.

A preparation is generated once from frozen inputs (`plan_generator.Inputs` and its goal)
and holds as long as the rest of its trame stays reachable by the rules it was generated
with. There is no tolerance in percent: a hypothesis falls when what is left of the trame
can no longer be reached from where the athlete stands.

- Volume held: the peak ahead is reached from what a closed week ran, climbing at most
  `ramp_max_pct` a build week that is not a deload. Each of the last two closed weeks of
  the version is read alone, the weeks planned low left out as the generator's start
  leaves them; the hypothesis falls only when both fall short, so one poor week never
  does (a mean over several weeks would carry it for as many). Once the peak week has
  passed, nothing is checked.
- Lineage position: each key session reaches its last planned dose before the taper, one
  step per exposure left outside the deloads, from the dose its lineage allows next. Only
  the variables still behind count, in the direction the template moves them.
- Availability: the minutes the habits leave for a week changed, and a week ahead no
  longer fits them at the frozen easy pace, or a peak the time capped could now climb.
  The days stated for one week are the draft's, never a hypothesis.
- Goal: the race's day, distance or terrain changed.
- Goal time, calibration: changed, they render the paces again; the trame holds.

A fallen volume, lineage, availability or goal calls for a new version (`replan`); a goal
time or a calibration alone, for its paces rendered again (`recalibrate`); nothing fallen,
the week's draft adapts within the bounds (`local`).

Only preparations are checked: the general preparation is generated again from the real
state at the end of each block, a recovery is low by design, and a rebuild ends by its own
condition (`redline.model.cycle_transitions`). The same arguments give the same review: it
reads neither the store nor the clock.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from . import iso_week
from .cycle_templates import CycleTemplates, KeySession
from .plan_generator import BUILD_PHASES, Goal, Trame, TrameRisk, Week
from .progression import Dose
from .vocabulary import CycleKind, HypothesisKind, HypothesisOutcome, PlanPhase

#: The fallen hypotheses that call for a new version.
REPLANNING = frozenset({HypothesisKind.VOLUME_HELD, HypothesisKind.LINEAGE_POSITION,
                        HypothesisKind.AVAILABILITY, HypothesisKind.GOAL})
#: A trame's volumes are rounded to 0.1 km: a reach within half of it reaches.
ROUNDING_KM = 0.05
#: The closed weeks a fallen volume persists over.
VOLUME_READINGS = 2
#: What the key of a plan review asked for fallen hypotheses starts with.
REVIEW_PREFIX = "plan-review:"


@dataclass(frozen=True)
class Observed:
    """The real state a version is checked against, read on `today`."""

    today: dt.date
    #: The running volume of the closed weeks not planned low, newest first, by week.
    volumes: tuple[tuple[str, float], ...]
    #: The dose each run lineage allows next, by the workout it follows.
    lineages: tuple[tuple[str, Dose], ...]
    #: The minutes the habits leave for the week after `today`; None: no limit.
    minutes_per_week: int | None
    #: The day of the calibration in force, None without one.
    calibration_on: dt.date | None
    #: The version's race as it stands, None when it is gone or its goal undeclared.
    goal: Goal | None


@dataclass(frozen=True)
class Fallen:
    """A hypothesis that no longer holds, with what was found against what it needs."""

    kind: HypothesisKind
    value: float | None = None
    reference: float | None = None
    #: The key session's workout, for a lineage.
    workout: str = ""
    #: The goal's field that changed: `on`, `distance_km` or `terrain`.
    field: str = ""


@dataclass(frozen=True)
class Review:
    outcome: HypothesisOutcome
    fallen: tuple[Fallen, ...] = ()

    @property
    def replanning(self) -> tuple[HypothesisKind, ...]:
        """The fallen kinds that call for a new version, each once, in a stable order."""
        return tuple(sorted({found.kind for found in self.fallen if found.kind in REPLANNING}))


HOLDS = Review(HypothesisOutcome.LOCAL)


def review_key(version_id: uuid.UUID, review: Review) -> str | None:
    """The plan review a version's fallen hypotheses ask for: one per version and set of
    fallen kinds, so two causes found the same day ask for one review, and a set already
    reviewed is never asked again."""
    kinds = review.replanning
    return f"{REVIEW_PREFIX}{version_id}:{'+'.join(kinds)}" if kinds else None


def _build(week: Week) -> bool:
    return week.phase in BUILD_PHASES and not week.deload


def _volume(trame: Trame, observed: Observed) -> Fallen | None:
    current = iso_week.of(observed.today)
    builds = [week for week in trame.weeks if _build(week)]
    if not builds:
        return None
    peak = max(week.volume_km for week in builds)
    peak_week = next(week.label for week in builds if week.volume_km == peak)
    read = [(label, km) for label, km in observed.volumes
            if label < current and trame.week(label) is not None][:VOLUME_READINGS]
    if peak_week < current or len(read) < VOLUME_READINGS:
        return None
    ramp = 1 + trame.hypotheses.ramp_max_pct / 100
    reaches = []
    for label, km in read:
        climbs = sum(1 for week in builds if label < week.label <= peak_week)
        reach = km * ramp ** climbs
        if reach >= peak - ROUNDING_KM:
            return None
        reaches.append(reach)
    return Fallen(HypothesisKind.VOLUME_HELD, round(reaches[0], 1), peak)


def _behind(dose: Dose, target: Dose, key: KeySession) -> float:
    """The steps from `dose` to `target`, counting only the variables still behind in
    the direction the template moves them."""
    steps = 0.0
    for name, wanted in target.values:
        now, step = dose.get(name), key.step.get(name)
        start, arrival = key.start.get(name), key.arrival.get(name)
        if now is None or not step or start is None or arrival is None or start == arrival:
            continue
        gap = (wanted - now) if arrival > start else (now - wanted)
        if gap > 0:
            steps += gap / step
    return steps


def _lineages(trame: Trame, observed: Observed, templates: CycleTemplates) -> list[Fallen]:
    current = iso_week.of(observed.today)
    weeks = {week.label: week for week in trame.weeks}
    lineages = dict(observed.lineages)
    fallen = []
    for workout in sorted({key.workout for key in trame.keys if key.dose is not None}):
        planned = [key for key in trame.keys
                   if key.workout == workout and key.dose is not None
                   and weeks[key.week].phase != PlanPhase.TAPER]
        dose = lineages.get(workout)
        if not planned or dose is None or planned[-1].week < current:
            continue
        target = planned[-1]
        assert target.dose is not None
        spec = templates.key_sessions(CycleKind.PREPARATION, trame.template,
                                      weeks[target.week].phase).get(workout)
        if spec is None:
            continue
        exposures = sum(1 for key in planned
                        if key.week >= current and not weeks[key.week].deload)
        needed = _behind(dose, target.dose, spec)
        if needed > max(exposures - 1, 0):
            fallen.append(Fallen(HypothesisKind.LINEAGE_POSITION, round(needed, 1),
                                 float(max(exposures - 1, 0)), workout))
    return fallen


def _capacity_km(minutes: int | None, pace_s: float) -> float | None:
    return None if minutes is None else minutes * 60 / pace_s


def _availability(trame: Trame, observed: Observed) -> Fallen | None:
    inputs = trame.hypotheses
    if observed.minutes_per_week == inputs.minutes_per_week or not inputs.easy_pace_s:
        return None
    current = iso_week.of(observed.today)
    ahead = [week for week in trame.weeks if week.label >= current]
    capacity = _capacity_km(observed.minutes_per_week, inputs.easy_pace_s)
    bound = capacity is not None and any(week.volume_km > capacity + ROUNDING_KM
                                         for week in ahead)
    capped = next((risk.value for risk in trame.risks if risk.kind == TrameRisk.TIME_BOUND),
                  None)
    lifted = (capped is not None and any(_build(week) for week in ahead)
              and (capacity is None or capacity > capped + ROUNDING_KM))
    if not (bound or lifted):
        return None
    return Fallen(HypothesisKind.AVAILABILITY,
                  None if observed.minutes_per_week is None else float(observed.minutes_per_week),
                  None if inputs.minutes_per_week is None else float(inputs.minutes_per_week))


def _goal(trame: Trame, observed: Observed) -> list[Fallen]:
    """The goal's changes. A goal frozen without its terrain was written before the
    terrain and the goal time were frozen: neither is compared."""
    frozen, now = trame.goal, observed.goal
    if frozen is None or now is None:
        return []
    fallen = [Fallen(HypothesisKind.GOAL, field=name)
              for name, changed in (("on", now.on != frozen.on),
                                    ("distance_km", now.distance_km != frozen.distance_km),
                                    ("terrain", frozen.terrain is not None
                                     and now.terrain != frozen.terrain))
              if changed]
    if frozen.terrain is not None and now.goal_time_s != frozen.goal_time_s:
        fallen.append(Fallen(HypothesisKind.GOAL_TIME,
                             None if now.goal_time_s is None else float(now.goal_time_s),
                             None if frozen.goal_time_s is None else float(frozen.goal_time_s)))
    return fallen


def check(trame: Trame, observed: Observed, templates: CycleTemplates) -> Review:
    """The review of a version's trame against what is observed: what fell, and the one
    outcome it calls for."""
    if trame.kind != CycleKind.PREPARATION:
        return HOLDS
    fallen: list[Fallen] = []
    for found in (_volume(trame, observed), _availability(trame, observed)):
        if found is not None:
            fallen.append(found)
    fallen += _lineages(trame, observed, templates)
    fallen += _goal(trame, observed)
    if observed.calibration_on != trame.hypotheses.calibration_on:
        fallen.append(Fallen(HypothesisKind.CALIBRATION))
    if any(found.kind in REPLANNING for found in fallen):
        outcome = HypothesisOutcome.REPLAN
    elif fallen:
        outcome = HypothesisOutcome.RECALIBRATE
    else:
        outcome = HypothesisOutcome.LOCAL
    return Review(outcome, tuple(fallen))
