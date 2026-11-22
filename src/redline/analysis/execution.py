"""A run's execution against its typed prescription: findings, each with its owner, and
the verdict the service rules before the coach writes.

The coach writes, he does not decide: what was the athlete's choice (a pace, a route),
what was the body's answer (a high HR at the usual pace) and what is only to be named
(a climb walked on a run whose climbs were to be run) are told apart here, from typed
fields and measures, never from prose.

What the intents ask (`redline.model.prescription`):
- the HR cap never applies on a climb; on the flat, an HR above the cap reads with the
  pace: faster than the reference easy pace is a choice, the same pace or slower is a
  state or sensor sign;
- a run's relief is a route choice, made before leaving, never mixed with the HR;
- `run_slow` names a walked climb; `power_hike` names a steep grade run and gives the
  share walked; `held` descents faster than the flat easy pace are an execution gap.

Not judged here: the effort's regularity of `race_effort` climbs and the cost of a run
against its expected RPE, which need the expected-dose model; a quality session keeps its
own judgment. Walking reads the cadence stream; without it, no walking is claimed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from redline.analysis import climbs
from redline.coach.fr import debrief as text
from redline.model import prescription, rulebook, terrain
from redline.model.notebook_views import KmSplit
from redline.model.streams import GAP_S, MOVING_MPS
from redline.model.vocabulary import (
    ClimbIntent,
    DebriefVerdict,
    DescentIntent,
    FindingKind,
    FindingOwner,
)

#: Strava's running cadence counts one leg: below this, the athlete walks.
WALK_CADENCE_PER_LEG = 70
#: A climb or a grade is walked when at least this share of its moving time is.
WALKED_SHARE = 0.5
#: Gain and loss measured by a watch drift: a relief within this share above its cap is
#: within the measure's noise.
RELIEF_NOISE_PCT = 10

OWNERS = {
    FindingKind.FLAT_TOO_FAST: FindingOwner.ATHLETE,
    FindingKind.STEEP_RUN: FindingOwner.ATHLETE,
    FindingKind.DESCENT_NOT_HELD: FindingOwner.ATHLETE,
    FindingKind.DPLUS_OVER: FindingOwner.ROUTE,
    FindingKind.DMINUS_OVER: FindingOwner.ROUTE,
    FindingKind.FLAT_HR_HIGH: FindingOwner.BODY,
    FindingKind.CLIMB_WALKED: FindingOwner.NOTE,
    FindingKind.STEEP_WALKED_SHARE: FindingOwner.NOTE,
}


@dataclass(frozen=True)
class Finding:
    """`value` is what was done and `reference` what it is held against: s/km for the
    paces, metres for the relief, the walked share against the grade threshold, the
    walked climbs against the climbs found. `kms` names the kilometres concerned."""

    kind: FindingKind
    value: float
    reference: float
    kms: tuple[int, ...] = ()

    @property
    def owner(self) -> FindingOwner:
        return OWNERS[self.kind]

    def as_json(self) -> dict:
        return {"kind": str(self.kind), "owner": str(self.owner), "value": self.value,
                "reference": self.reference, "kms": list(self.kms)}


@dataclass(frozen=True)
class Execution:
    verdict: DebriefVerdict
    findings: tuple[Finding, ...]

    def as_json(self) -> dict:
        return {"verdict": str(self.verdict),
                "findings": [finding.as_json() for finding in self.findings]}


@dataclass(frozen=True)
class Stretch:
    """A climb or a steep grade as run: its first kilometre, moving and walked time."""

    km: int
    moving_s: float
    walked_s: float

    @property
    def walked(self) -> bool:
        return self.moving_s > 0 and self.walked_s / self.moving_s >= WALKED_SHARE


def _stretch(streams: dict, start_m: float, end_m: float) -> Stretch | None:
    """Moving and walked time between two distances; None without a cadence stream."""
    time, distance, cadence = streams.get("t"), streams.get("d"), streams.get("cad")
    if not time or not distance or not cadence:
        return None
    speed = streams.get("v")
    moving = walked = 0.0
    for k in range(1, len(time)):
        here = distance[k]
        if here is None or not start_m < here <= end_m:
            continue
        step = time[k] - time[k - 1]
        if step > GAP_S or (speed is not None and (speed[k] or 0) < MOVING_MPS):
            continue
        moving += step
        if cadence[k] is not None and cadence[k] < WALK_CADENCE_PER_LEG:
            walked += step
    return Stretch(int(start_m // 1000) + 1, moving, walked)


def _climbs(streams: dict | None) -> list[climbs.Climb]:
    if not streams or not streams.get("d") or not streams.get("alt"):
        return []
    pairs = [(d, z) for d, z in zip(streams["d"], streams["alt"], strict=False)
             if d is not None and z is not None]
    if len(pairs) < 2:
        return []
    distance, elevation = (np.array(values, dtype=float) for values in zip(*pairs, strict=True))
    return climbs.detect(distance, elevation)


def _pace(rows: Sequence[KmSplit]) -> float:
    metres = sum(row["dist_m"] for row in rows)
    return sum(row["moving_s"] for row in rows) * 1000 / metres


def _terrain(row: KmSplit) -> terrain.Terrain:
    return terrain.of(row["dist_m"], row["dplus"], row["dminus"])


def _flat(session: dict, splits: Sequence[KmSplit], reference_pace_s: float | None,
          hr_trusted: bool) -> list[Finding]:
    """The flat HR above the cap, read with the pace against the reference easy pace."""
    cap, margin = session.get("hr_cap_bpm"), rulebook.current().parameters.split_hr_margin_bpm
    if (cap is None or margin is None or reference_pace_s is None or not hr_trusted
            or prescription.has_efforts(session)):
        return []
    above = [row for row in splits if _terrain(row) is terrain.Terrain.FLAT
             and row["hr"] is not None and row["hr"] > cap + margin]
    if not above:
        return []
    pace, kms = _pace(above), tuple(row["n"] for row in above)
    kind = FindingKind.FLAT_TOO_FAST if pace < reference_pace_s else FindingKind.FLAT_HR_HIGH
    return [Finding(kind, round(pace), round(reference_pace_s), kms)]


def _relief(session: dict, dplus: float | None, dminus: float | None) -> list[Finding]:
    out = []
    for kind, done, cap in ((FindingKind.DPLUS_OVER, dplus, session.get("dplus_cap_m")),
                            (FindingKind.DMINUS_OVER, dminus, session.get("dminus_cap_m"))):
        if done is not None and cap is not None and done > cap * (1 + RELIEF_NOISE_PCT / 100):
            out.append(Finding(kind, float(done), float(cap)))
    return out


def _climb_intent(session: dict, streams: dict | None) -> list[Finding]:
    intent = prescription.climb_intent(session)
    if intent not in (ClimbIntent.RUN_SLOW, ClimbIntent.POWER_HIKE) or not streams:
        return []
    found = _climbs(streams)
    if intent is ClimbIntent.RUN_SLOW:
        walked = [stretch.km for climb in found
                  for stretch in [_stretch(streams, climb.start_m, climb.end_m)]
                  if stretch is not None and stretch.walked]
        return ([Finding(FindingKind.CLIMB_WALKED, float(len(walked)), float(len(found)),
                         tuple(dict.fromkeys(walked)))] if walked else [])
    threshold = float(session["power_hike_from_pct"])
    steep = [stretch for climb in found for section in climb.sections
             if section.grade_pct >= threshold
             for stretch in [_stretch(streams, section.start_m, section.end_m)]
             if stretch is not None and stretch.moving_s > 0]
    if not steep:
        return []
    share = sum(stretch.walked_s for stretch in steep) / sum(stretch.moving_s for stretch in steep)
    out = [Finding(FindingKind.STEEP_WALKED_SHARE, round(share, 2), threshold)]
    run = tuple(dict.fromkeys(stretch.km for stretch in steep if not stretch.walked))
    if run:
        out.append(Finding(FindingKind.STEEP_RUN, float(len(run)), threshold, run))
    return out


def _descents(session: dict, splits: Sequence[KmSplit],
              reference_pace_s: float | None) -> list[Finding]:
    if prescription.descent_intent(session) is not DescentIntent.HELD or reference_pace_s is None:
        return []
    fast = [row for row in splits if _terrain(row) is terrain.Terrain.DESCENT
            and row["pace_s"] < reference_pace_s]
    if not fast:
        return []
    return [Finding(FindingKind.DESCENT_NOT_HELD, round(_pace(fast)), round(reference_pace_s),
                    tuple(row["n"] for row in fast))]


def judge(session: dict | None, splits: Sequence[KmSplit], streams: dict | None, *,
          dplus: float | None, dminus: float | None, reference_pace_s: float | None,
          hr_trusted: bool = True) -> Execution:
    """The findings of a run against its prescription and the verdict they make."""
    if session is None:
        return Execution(DebriefVerdict.UNDETERMINED, ())
    findings = [*_relief(session, dplus, dminus),
                *_flat(session, splits, reference_pace_s, hr_trusted),
                *_climb_intent(session, streams),
                *_descents(session, splits, reference_pace_s)]
    if any(finding.owner in (FindingOwner.ATHLETE, FindingOwner.ROUTE) for finding in findings):
        verdict = DebriefVerdict.EXECUTION_GAP
    elif splits or findings:
        verdict = DebriefVerdict.CONFORMING
    else:
        verdict = DebriefVerdict.UNDETERMINED
    return Execution(verdict, tuple(findings))


def render(execution: Execution) -> list[str]:
    """The verdict and its findings as the coach reads them, each saying whose it is."""
    lines = [text.VERDICT.format(verdict=text.VERDICTS[execution.verdict])]
    for finding in execution.findings:
        lines.append(text.FINDINGS[finding.kind].format(
            value=text.number(finding.value), reference=text.number(finding.reference),
            pace=text.pace(finding.value), reference_pace=text.pace(finding.reference),
            kms=text.kilometres(finding.kms) if finding.kms else "",
            share=round(finding.value * 100), signed=text.signed(
                round((finding.value - finding.reference) / finding.reference * 100))
            if finding.reference else ""))
    return lines
