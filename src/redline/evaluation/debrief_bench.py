"""The debrief bench: fictional runs against their prescription, with the verdict and the
findings a fair coach expects.

Each case is a typed prescription and a synthetic run (legs of steady pace, grade,
cadence and HR), may carry the repetitions read from its laps ([seconds, metres] each),
the athlete's easy HR by grade ([grade %, HR] each) and its cost: the declared RPE, the ranges expected of the
dose run and of the dose prescribed, the signs read off the run. The bench rules each one
with the production code (`redline.analysis.execution`) and counts what matters to the
athlete: an unjustified reproach is a finding the coach would correct (an athlete or
route choice), or an RPE it would speak of, that the case does not expect; a missed
intervention is an expected one the verdict lacks. Cases are added from every reproach
the athlete contests.

A quality case may also say what its verdict does to the NEXT session (`next`): the
progression engine (`redline.analysis.progression`) reads the session as production
does, its execution gaps from the verdict and its RPE against the range of the dose run,
with the facts that follow it (reserve, effort pain, next morning, the runs after), and
proposes the next dose. A held session at a high RPE moves the dose up; the same
session with its last repetition pushed holds it. The chronology is kept: the proposal
reads only what is known once the runs that follow are in.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from redline.analysis import execution, expected_rpe, grade_hr, ledger, progression_runs
from redline.analysis.progression import propose
from redline.analysis.progression_readings import FOLLOWING_RUNS, interval_exposure
from redline.model import session_kinds
from redline.model.progression import Bound, Lineage, Move, default
from redline.model.vocabulary import FindingKind, FindingOwner, RpeSide, RpeSign
from redline.service import session_detail

CASES = Path(__file__).with_name("debrief-cases.json")
#: The reference easy pace of the fictional athlete, in s/km (6:14/km).
REFERENCE_PACE_S = 374.0
CORRECTED = (FindingOwner.ATHLETE, FindingOwner.ROUTE)
#: The day a quality case is run, and the zone its pains are read on.
SESSION_DAY = dt.date(2030, 3, 5)
ZONE = "shin"
#: The fictional athlete's quality lineage: what an instance declares in `[progression]`.
LINEAGE_PARAMETERS = (("reserve_min", 1.0), ("following_pain_max", 2.0), ("pain_cap", 2.0))


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
    #: What the verdict does to the next session, and what the case expects; None when
    #: the case says nothing of it.
    next_move: Move | None = None
    next_expected: Move | None = None

    @property
    def next_matches(self) -> bool:
        return self.next_move == self.next_expected

    @property
    def passed(self) -> bool:
        return (self.verdict_matches and self.next_matches
                and not (self.unjustified or self.missed or self.other))


def _expectation(values: list[float] | None) -> expected_rpe.Expectation | None:
    """[expected, low, high] as a range calibrated on a settled history."""
    if values is None:
        return None
    expected, low, high = values
    return expected_rpe.Expectation(expected, int(low), int(high), sessions=7, residual=0.3)


def _cost(data: dict | None) -> execution.Cost | None:
    if data is None:
        return None
    return execution.Cost(
        data.get("declared"), _expectation(data.get("run")), _expectation(data.get("prescribed")),
        RpeSide(data.get("previous", RpeSide.UNKNOWN)),
        tuple(RpeSign(sign) for sign in data.get("signs") or ()))


def _curve(points: list[list[float]] | None) -> grade_hr.Curve | None:
    """[grade %, HR] bins as a curve kept on a settled history."""
    if not points:
        return None
    return grade_hr.Curve(dt.date.min, grade_hr.MIN_RUNS, tuple(
        grade_hr.Bin(int(grade), float(hr), grade_hr.MIN_STRETCHES, grade_hr.MIN_RUNS)
        for grade, hr in points))


def rule(case: dict) -> execution.Execution:
    streams = synthetic_streams(case["legs"])
    session = {"id": case["id"], "kind": "run", **case["session"]}
    return execution.judge(session, session_detail.km_splits(streams) or [], streams,
                           dplus=case.get("dplus"), dminus=case.get("dminus"),
                           reference_pace_s=case.get("reference_pace_s", REFERENCE_PACE_S),
                           cost=_cost(case.get("cost")),
                           reps=[{"time_s": seconds, "dist_m": metres}
                                 for seconds, metres in case.get("reps") or ()],
                           curve=_curve(case.get("grade_hr")))


def _lineage(dose: dict[str, float]) -> Lineage:
    """Room on the ladder above the case's dose, so a rise is never a ladder end."""
    return Lineage("bench", "intervals", (
        ("reps", Bound(1, dose["reps"] + 2, 1)),
        ("rep_seconds", Bound(60, dose["rep_seconds"] + 300, 60)),
        ("recovery_seconds", Bound(30, dose["recovery_seconds"], 30))), LINEAGE_PARAMETERS)


def next_move(case: dict, ruled: execution.Execution) -> Move | None:
    """The move the progression engine proposes for the next session once this one is
    ruled and the facts that follow it are in; None for a case without `next`."""
    expected = case.get("next")
    if expected is None:
        return None
    family = default()["intervals"]
    dose = progression_runs.planned_dose(family, case["session"])
    if dose is None:
        raise ValueError(f"case {case['id']}: `next` needs a typed quality dose")
    after = expected.get("after") or {}
    cost = case.get("cost") or {}
    day = dt.timedelta(days=1)
    rows = [ledger.Row(SESSION_DAY - day, session_kinds.WAKE_TRACE, pains={ZONE: 0.0}),
            ledger.Row(SESSION_DAY, case["session"]["workout"], km=10, hours=1.0,
                       rpe=cost.get("declared"), reserve=after.get("reserve", 1.0),
                       pains={ZONE: after.get("pain", 0.5)}),
            ledger.Row(SESSION_DAY + day, session_kinds.WAKE_TRACE,
                       pains={ZONE: after.get("wake", 0.0)}),
            *(ledger.Row(SESSION_DAY + day * (index + 1), session_kinds.EASY, km=8, hours=0.8,
                         pains={ZONE: after.get("following_pain", 0.5)})
              for index in range(FOLLOWING_RUNS))]
    exposure = interval_exposure(
        rows, rows[1], dose, [ZONE], rows[1].reserve,
        over_range=progression_runs.over_range(_expectation(cost.get("run")), rows[1].rpe),
        gaps=progression_runs.athlete_gaps([finding.as_json() for finding in ruled.findings]))
    return propose(family, _lineage(dict(dose.values)), [exposure], current=dose,
                   today=SESSION_DAY + day * (FOLLOWING_RUNS + 1)).move


def outcome(case: dict) -> Outcome:
    ruled = rule(case)
    found = {finding.kind for finding in ruled.findings}
    expected = {FindingKind(kind) for kind in case["expected"]["findings"]}
    extra, lacking = found - expected, expected - found
    spoken = {finding.kind for finding in ruled.findings
              if finding.kind in execution.RPE_FINDINGS.values() and finding.signs}
    to_speak = {FindingKind(kind) for kind in case["expected"].get("spoken") or ()}
    return Outcome(
        case["id"], str(ruled.verdict) == case["expected"]["verdict"],
        unjustified=tuple(sorted({kind for kind in extra if execution.OWNERS[kind] in CORRECTED}
                                 | (spoken - to_speak))),
        missed=tuple(sorted({kind for kind in lacking if execution.OWNERS[kind] in CORRECTED}
                            | (to_speak - spoken))),
        other=tuple(sorted(kind for kind in extra ^ lacking
                           if execution.OWNERS[kind] not in CORRECTED)),
        next_move=next_move(case, ruled),
        next_expected=Move(case["next"]["move"]) if case.get("next") else None)


def run(path: Path = CASES) -> list[Outcome]:
    return [outcome(case) for case in json.loads(path.read_text())]
