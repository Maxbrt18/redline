"""Progression: how a dose moves from one exposure to the next, declared and auditable.

One engine serves every family of sessions that should progress: a strength exercise, a
threshold session, the carbohydrate target of a long run, the easy volume the gate opens.
A family declares, as data, the method:

- its **ladder**: the dose variables in the order they move, and whether a variable
  returns to its minimum when the next one steps up (double progression);
- its **criteria**: what an exposure must show, each read from typed signals, each with
  what its failure calls for (hold or step down);
- its **rule**: ordered clauses mapping the verdicts of the latest exposures to a move;
- the **vetoes** that stop a rise whatever the rule says.

A lineage is one thing followed from exposure to exposure (an exercise, a workout, the
fuelling of long runs). It names its family and gives the bounds and steps of each ladder
variable, plus the fixed parameters its criteria refer to.

The engine (`redline.analysis.progression`) turns a lineage's history into a proposal
that cites the clause, the verdicts and the facts behind them. A rise never passes a veto,
and a reading that was not taken never passes a criterion.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import ConfigError
from .schema import Schema, SchemaError
from .schema import field as spec

DEFAULT = Path(__file__).with_name("progression.default.toml")
SLUG = r"[a-z0-9][a-z0-9-]{0,39}"


class Move(StrEnum):
    UP = "up"
    HOLD = "hold"
    DOWN = "down"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    #: A reading the criterion needs was not taken, or is not in yet.
    UNKNOWN = "unknown"


class Severity(StrEnum):
    """What the failure of a criterion calls for on its own."""

    HOLD = "hold"
    DOWN = "down"


class StepMode(StrEnum):
    """How far one step of a variable goes."""

    #: Exactly one step: the equipment or the format fixes it (a 2 kg dumbbell, one rep).
    EXACT = "exact"
    #: Any value up to one step: a ceiling, the prescriber picks within it.
    AT_MOST = "at_most"
    #: The rule grants a direction only; the magnitude is the prescriber's.
    FREE = "free"


class Unit(StrEnum):
    """What one exposure of the family is."""

    SESSION = "session"
    WEEK = "week"


class Anchor(StrEnum):
    """Which dose of the latest exposure the next step starts from."""

    #: What was done when it is known, else what was prescribed.
    PERFORMED = "performed"
    PRESCRIBED = "prescribed"


class Signal(StrEnum):
    """The typed readings an exposure can carry, computed from the ledger."""

    SESSION_RPE = "session_rpe"
    #: Whole repetitions still possible at the same effort when the session stopped.
    RESERVE_REPS = "reserve_reps"
    #: The fewest repetitions of the working sets, every side counted apart.
    SET_REPS_MIN = "set_reps_min"
    #: The athlete's 0-10 difficulty of one exercise.
    DIFFICULTY = "difficulty"
    #: Peak pain during or right after the exposure, one reading per watched zone.
    EFFORT_PAIN = "effort_pain"
    #: Peak effort pain of the runs that follow the exposure, one reading per zone.
    FOLLOWING_PAIN = "following_pain"
    #: The next morning's wake trace minus the zone's rest baseline.
    WAKE_ABOVE_BASE = "wake_above_base"
    GUT_SYMPTOMS = "gut_symptoms"
    CARBS_TAKEN = "carbs_taken"
    #: The boxes of the gate, read over a closed week.
    WEEK_EFFORT_PEAK = "week_effort_peak"
    WEEK_PEAK_RISE = "week_peak_rise"
    WEEK_WAKES_ABOVE_BASE = "week_wakes_above_base"
    WEEK_WAKE_RISING = "week_wake_rising"
    WEEK_PERSISTENT_WAKES = "week_persistent_wakes"
    WEEK_EASY_HR_DRIFTS = "week_easy_hr_drifts"
    WEEK_SLEEP_DEBT = "week_sleep_debt"


class VetoKind(StrEnum):
    """A state outside the lineage that stops a rise."""

    #: The gate (rule po) did not open this week.
    GATE = "gate"
    #: Rules n3 and n4 removed quality.
    QUALITY_HOLD = "quality_hold"
    #: The weeks before a typed race.
    TAPER = "taper"
    #: An assimilation week of the rulebook (rule n2).
    ASSIMILATION = "assimilation"
    #: The week's rise budget is spent (rule n1, one change at a time in a routine).
    BUDGET = "budget"


class Disruption(StrEnum):
    """Why an exposure says little about the dose: a cause outside the training."""

    ILLNESS = "illness"
    HEAT = "heat"
    SCHEDULE = "schedule"
    EQUIPMENT = "equipment"
    TERRAIN = "terrain"
    OTHER = "other"


# ------------------------------------------------------------------ the dose

@dataclass(frozen=True)
class Dose:
    """A point of a ladder: one value per variable, in the ladder's order."""

    values: tuple[tuple[str, float], ...]

    @classmethod
    def of(cls, **values: float) -> Dose:
        return cls(tuple(values.items()))

    def get(self, name: str) -> float | None:
        return dict(self.values).get(name)

    def with_value(self, name: str, value: float) -> Dose:
        return Dose(tuple((key, value if key == name else current)
                          for key, current in self.values))

    def __str__(self) -> str:
        return ", ".join(f"{name}={value:g}" for name, value in self.values)


@dataclass(frozen=True)
class Variable:
    """A ladder variable as the family declares it: its place and its behaviour."""

    name: str
    mode: StepMode = StepMode.EXACT
    #: On a carry, the variable goes back to its minimum (reps after a load rise);
    #: otherwise it stays where it is (a ceiling of effort once raised).
    resets: bool = True


@dataclass(frozen=True)
class Bound:
    """A lineage's range and step for one variable."""

    minimum: float
    maximum: float
    step: float


# ------------------------------------------------------------------ the response

@dataclass(frozen=True)
class Reading:
    """One reading of an exposure. `value` None: expected, but not taken (yet)."""

    signal: Signal
    value: float | None
    #: A watched zone for a pain reading, empty otherwise.
    scope: str = ""
    #: The ledger fact the value comes from, cited by the proposal.
    fact: str = ""


@dataclass(frozen=True)
class Exposure:
    """One occurrence of a lineage, and what it showed."""

    on: dt.date
    prescribed: Dose | None
    performed: Dose | None
    readings: tuple[Reading, ...] = ()
    disrupted: Disruption | None = None

    def readings_of(self, signal: Signal) -> tuple[Reading, ...]:
        return tuple(reading for reading in self.readings if reading.signal == signal)

    def dose(self, anchor: Anchor) -> Dose | None:
        if anchor == Anchor.PERFORMED and self.performed is not None:
            return self.performed
        return self.prescribed


# ------------------------------------------------------------------ the method

class Side(StrEnum):
    AT_MOST = "at_most"
    AT_LEAST = "at_least"


@dataclass(frozen=True)
class Criterion:
    """A test on one signal. Its threshold is a number, or the name of a dose variable
    or lineage parameter (`ref`), so that "RPE within the band" reads the band of the
    dose that was run."""

    id: str
    signal: Signal
    side: Side
    threshold: float | None = None
    ref: str | None = None
    severity: Severity = Severity.HOLD


@dataclass(frozen=True)
class Clause:
    """One line of a rule: a condition on the latest verdicts, and the move it gives.

    Exactly one condition is set. `passes_in_a_row` counts the latest exposures at the
    current dose where every criterion passed; `fails_in_a_row` the latest exposures
    with at least one failure, whatever their dose.
    """

    id: str
    move: Move
    fails_at_least: int | None = None
    severe: bool = False
    unknowns_at_least: int | None = None
    passes_in_a_row: int | None = None
    fails_in_a_row: int | None = None


@dataclass(frozen=True)
class Family:
    slug: str
    unit: Unit
    ladder: tuple[Variable, ...]
    criteria: tuple[Criterion, ...]
    rule: tuple[Clause, ...]
    #: Signals an exposure must carry, known, to count as evidence at all.
    requires: tuple[Signal, ...] = ()
    vetoes: tuple[VetoKind, ...] = ()
    anchor: Anchor = Anchor.PERFORMED
    #: Steps down from the last tolerated dose when the lineage comes back after a gap.
    reentry_steps: int = 1


@dataclass(frozen=True)
class Lineage:
    """One thing followed from exposure to exposure, in its family."""

    slug: str
    family: str
    bounds: tuple[tuple[str, Bound], ...]
    parameters: tuple[tuple[str, float], ...] = ()
    #: Lineages of one group share a rise budget (one exercise of a routine at a time).
    group: str = ""

    def bound(self, name: str) -> Bound | None:
        return dict(self.bounds).get(name)

    def parameter(self, name: str) -> float | None:
        return dict(self.parameters).get(name)


@dataclass(frozen=True)
class Veto:
    """An active veto, as computed by the rule that owns it."""

    kind: VetoKind
    rule: str
    fact: str


# ------------------------------------------------------------------ the proposal

@dataclass(frozen=True)
class Judgement:
    """One criterion judged on one exposure."""

    criterion: str
    verdict: Verdict
    severity: Severity
    #: The worst reading, as compared, and the threshold it was compared with.
    value: float | None = None
    threshold: float | None = None
    fact: str = ""


@dataclass(frozen=True)
class Proposal:
    """The next dose of a lineage, with everything that authorises it."""

    lineage: str
    family: str
    #: What the rule alone said, before any veto.
    rule_move: Move
    move: Move
    clause: str
    anchor: Dose | None
    dose: Dose | None
    #: The highest dose a plan may prescribe; None when nothing bounds it.
    ceiling: Dose | None
    judgements: tuple[Judgement, ...] = ()
    vetoes: tuple[Veto, ...] = ()
    #: Signals to ask for: the exposure cannot count until they are read.
    missing: tuple[Signal, ...] = ()
    #: Exposures that carried no required reading, left out of the evidence.
    unread: tuple[dt.date, ...] = ()
    #: The lineage comes back after a gap: the dose steps down, never up.
    reentry: bool = False
    #: The rule's move fell off the ladder: a rise needs a new exercise or range, a step
    #: down an easier variant.
    ladder_end: bool = False
    #: Consecutive passes at the current dose, what the rise budget favours.
    streak: int = 0
    evidence: tuple[dt.date, ...] = field(default=())


# ------------------------------------------------------------------ declaration

THRESHOLDS = ("at_most", "at_least", "at_most_ref", "at_least_ref")


def _criterion_check(data: dict) -> list[str]:
    stated = [name for name in THRESHOLDS if data.get(name) is not None]
    return [] if len(stated) == 1 else [
        f"a criterion states exactly one threshold ({', '.join(THRESHOLDS)})"]


def _clause_check(data: dict) -> list[str]:
    conditions = [name for name in CONDITIONS if data.get(name) not in (None, False)]
    return [] if len(conditions) == 1 else [
        f"a clause states exactly one condition ({', '.join(CONDITIONS)})"]


CONDITIONS = ("fails_at_least", "severe", "unknowns_at_least", "passes_in_a_row",
              "fails_in_a_row")

FAMILY_SCHEMA = Schema("[[families]]", [
    spec("slug", str, pattern=SLUG),
    spec("unit", str, choices=tuple(Unit)),
    spec("anchor", str, required=False, choices=tuple(Anchor)),
    spec("reentry_steps", int, required=False, minimum=0, maximum=4),
    spec("requires", list, required=False, item=spec("signal", str, choices=tuple(Signal))),
    spec("vetoes", list, required=False, item=spec("veto", str, choices=tuple(VetoKind))),
    spec("ladder", list, maximum=4, item=spec("variable", dict, schema=Schema(
        "[[families.ladder]]", [
            spec("name", str, pattern=r"[a-z][a-z0-9_]{0,39}"),
            spec("mode", str, required=False, choices=tuple(StepMode)),
            spec("resets", bool, required=False),
        ]))),
    spec("criteria", list, minimum=1, maximum=12, item=spec("criterion", dict, schema=Schema(
        "[[families.criteria]]", [
            spec("id", str, pattern=SLUG),
            spec("signal", str, choices=tuple(Signal)),
            spec("at_most", float, required=False),
            spec("at_least", float, required=False),
            spec("at_most_ref", str, required=False, pattern=r"[a-z][a-z0-9_]{0,39}",
                 note="a dose variable or a lineage parameter"),
            spec("at_least_ref", str, required=False, pattern=r"[a-z][a-z0-9_]{0,39}",
                 note="a dose variable or a lineage parameter"),
            spec("severity", str, required=False, choices=tuple(Severity)),
        ], check=_criterion_check))),
    spec("rule", list, minimum=1, maximum=8, item=spec("clause", dict, schema=Schema(
        "[[families.rule]]", [
            spec("id", str, pattern=SLUG),
            spec("move", str, choices=tuple(Move)),
            spec("fails_at_least", int, required=False, minimum=1, maximum=12),
            spec("severe", bool, required=False),
            spec("unknowns_at_least", int, required=False, minimum=1, maximum=12),
            spec("passes_in_a_row", int, required=False, minimum=1, maximum=8),
            spec("fails_in_a_row", int, required=False, minimum=1, maximum=8),
        ], check=_clause_check))),
])
DECLARATION_SCHEMA = Schema("progression.toml", [
    spec("families", list, minimum=1, maximum=24,
         item=spec("family", dict, schema=FAMILY_SCHEMA)),
])


class DeclarationError(ConfigError):
    """Unreadable or non-conforming progression declaration."""


def _criterion(data: dict) -> Criterion:
    name = next(name for name in THRESHOLDS if data.get(name) is not None)
    value = data[name]
    return Criterion(
        id=data["id"], signal=Signal(data["signal"]), side=Side(name.removesuffix("_ref")),
        threshold=None if name.endswith("_ref") else float(value),
        ref=value if name.endswith("_ref") else None,
        severity=Severity(data.get("severity") or Severity.HOLD))


def _family(data: dict) -> Family:
    return Family(
        slug=data["slug"], unit=Unit(data["unit"]),
        ladder=tuple(Variable(item["name"], StepMode(item.get("mode") or StepMode.EXACT),
                              item.get("resets", True)) for item in data["ladder"]),
        criteria=tuple(_criterion(item) for item in data["criteria"]),
        rule=tuple(Clause(id=item["id"], move=Move(item["move"]),
                          **{name: item[name] for name in CONDITIONS if name in item})
                   for item in data["rule"]),
        requires=tuple(Signal(item) for item in data.get("requires") or ()),
        vetoes=tuple(VetoKind(item) for item in data.get("vetoes") or ()),
        anchor=Anchor(data.get("anchor") or Anchor.PERFORMED),
        reentry_steps=data.get("reentry_steps", 1),
    )


def parse(text: str, source: Path) -> dict[str, Family]:
    """The families of a declaration, by slug. Every problem is reported at once."""
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DeclarationError(f"{source} unreadable: {exc}") from None
    try:
        DECLARATION_SCHEMA.validate(payload, str(source))
    except SchemaError as exc:
        raise DeclarationError(str(exc)) from None
    families = [_family(item) for item in payload["families"]]
    slugs = [family.slug for family in families]
    if doubled := sorted({slug for slug in slugs if slugs.count(slug) > 1}):
        raise DeclarationError(f"{source}: duplicate family: {', '.join(doubled)}")
    return {family.slug: family for family in families}


def default() -> dict[str, Family]:
    """The engine's generic families; an instance replaces them as a whole."""
    return parse(DEFAULT.read_text(encoding="utf-8"), DEFAULT)
