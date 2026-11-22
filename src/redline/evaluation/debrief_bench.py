"""The debrief bench: fictional runs against their prescription, with the verdict and the
findings a fair coach expects.

Each case is a typed prescription and a synthetic run (legs of steady pace, grade,
cadence and HR). The bench rules each one with the production code
(`redline.analysis.execution`) and counts what matters to the athlete: an unjustified
reproach is a finding the coach would correct (an athlete or route choice) that the case
does not expect; a missed intervention is an expected one the verdict lacks. Cases are
added from every reproach the athlete contests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from redline.analysis import execution
from redline.model.vocabulary import FindingKind, FindingOwner
from redline.service import session_detail

CASES = Path(__file__).with_name("debrief-cases.json")
#: The reference easy pace of the fictional athlete, in s/km (6:14/km).
REFERENCE_PACE_S = 374.0
CORRECTED = (FindingOwner.ATHLETE, FindingOwner.ROUTE)


def synthetic_streams(legs: list[list[float]]) -> dict:
    """One sample a second along `legs`: [metres, s/km, grade %, cadence per leg, HR]."""
    t, d, alt, v, cad, hr = [0], [0.0], [100.0], [0.0], [85], [140]
    for metres, pace, grade, cadence, beat in legs:
        speed = 1000 / pace
        for _ in range(int(metres / speed)):
            t.append(t[-1] + 1)
            d.append(round(d[-1] + speed, 1))
            alt.append(round(alt[-1] + speed * grade / 100, 2))
            v.append(round(speed, 2))
            cad.append(int(cadence))
            hr.append(int(beat))
    return {"t": t, "d": d, "alt": alt, "v": v, "cad": cad, "hr": hr}


@dataclass(frozen=True)
class Outcome:
    case_id: str
    verdict_matches: bool
    unjustified: tuple[FindingKind, ...] = ()
    missed: tuple[FindingKind, ...] = ()
    other: tuple[FindingKind, ...] = field(default=())

    @property
    def passed(self) -> bool:
        return self.verdict_matches and not (self.unjustified or self.missed or self.other)


def rule(case: dict) -> execution.Execution:
    streams = synthetic_streams(case["legs"])
    session = {"id": case["id"], "kind": "run", **case["session"]}
    return execution.judge(session, session_detail.km_splits(streams) or [], streams,
                           dplus=case.get("dplus"), dminus=case.get("dminus"),
                           reference_pace_s=case.get("reference_pace_s", REFERENCE_PACE_S))


def outcome(case: dict) -> Outcome:
    ruled = rule(case)
    found = {finding.kind for finding in ruled.findings}
    expected = {FindingKind(kind) for kind in case["expected"]["findings"]}
    extra, lacking = found - expected, expected - found
    return Outcome(
        case["id"], str(ruled.verdict) == case["expected"]["verdict"],
        unjustified=tuple(sorted(kind for kind in extra
                                 if execution.OWNERS[kind] in CORRECTED)),
        missed=tuple(sorted(kind for kind in lacking if execution.OWNERS[kind] in CORRECTED)),
        other=tuple(sorted(kind for kind in extra ^ lacking
                           if execution.OWNERS[kind] not in CORRECTED)))


def run(path: Path = CASES) -> list[Outcome]:
    return [outcome(case) for case in json.loads(path.read_text())]
