"""The instance's pain scales: its levels, and the roles the engine reads them by.

A pain value means something only on the scale the athlete rates it on. A behavioural
table may put "stride altered" at 2, where a generic 0–10 scale is still in its safe
zone. So the engine never compares a pain to a number of its own: each instance declares its
scales in `rulebook.toml`, and the rules ask a scale a question about a ROLE ("is this
morning above the base?", "is this effort above the cap?").

No value is ever converted from one scale to another. A reading is stored as the value
of the level the athlete chose, and is only ever compared with the levels of the same
scale.

Two scales:

- **morning trace** (`wake-trace` rows): the athlete picks one level by its slug. Every
  level declares a role, in this order: `below_base`, exactly one `base` (the usual
  morning), `above_base`.
- **effort pain**: a number between the lowest and highest level, in steps of `step`.
  The labelled levels anchor the reading; one is the `cap` (stop above it) and a
  higher one is `severe` (harm already done).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from .schema import Field, Schema, field

SLUG = r"[a-z0-9][a-z0-9-]{0,39}"
#: The bounds every pain value is stored within (`pain_readings`, CHECK constraints).
LOWEST, HIGHEST = 0, 10


class TraceRole(StrEnum):
    """Where a morning-trace level stands against the athlete's usual morning."""

    BELOW_BASE = "below_base"
    BASE = "base"
    ABOVE_BASE = "above_base"


class EffortRole(StrEnum):
    """What an effort-pain level means for the session under way."""

    CAP = "cap"
    SEVERE = "severe"


#: The roles in the order their levels must follow, lowest value first.
TRACE_ORDER = (TraceRole.BELOW_BASE, TraceRole.BASE, TraceRole.ABOVE_BASE)
EFFORT_ORDER = (EffortRole.CAP, EffortRole.SEVERE)


@dataclass(frozen=True)
class Level:
    value: float
    label: str
    role: str | None = None
    slug: str | None = None


def _values(levels: list[dict]) -> list[float]:
    return [float(level["value"]) for level in levels]


def _ascending(levels: list[dict]) -> list[str]:
    values = _values(levels)
    if any(later <= earlier for earlier, later in pairwise(values)):
        return [f"levels: values must strictly increase ({', '.join(f'{v:g}' for v in values)})"]
    return []


def _roles_in_order(roles: list[str], order: tuple[str, ...]) -> list[str]:
    ranks = [order.index(role) for role in roles]
    if ranks != sorted(ranks):
        return [f"levels: roles out of order ({', '.join(roles)}); expected "
                f"{' < '.join(order)} from the lowest value up"]
    return []


def _trace_check(payload: dict) -> list[str]:
    levels = payload["levels"]
    roles = [level["role"] for level in levels]
    problems = _ascending(levels)
    if roles.count(TraceRole.BASE) != 1:
        problems.append(f"levels: exactly one level has role base (found "
                        f"{roles.count(TraceRole.BASE)})")
    problems += _roles_in_order(roles, TRACE_ORDER)
    slugs = [level["slug"] for level in levels]
    if doubled := sorted({slug for slug in slugs if slugs.count(slug) > 1}):
        problems.append(f"levels: duplicate slug {', '.join(doubled)}")
    return problems


def _effort_check(payload: dict) -> list[str]:
    levels = payload["levels"]
    roles = [level["role"] for level in levels if level.get("role")]
    problems = _ascending(levels)
    for role in EffortRole:
        if roles.count(role) != 1:
            problems.append(f"levels: exactly one level has role {role} "
                            f"(found {roles.count(role)})")
    problems += _roles_in_order(roles, EFFORT_ORDER)
    step = payload["step"]
    if off := [f"{value:g}" for value in _values(levels) if not _on_step(value, step)]:
        problems.append(f"levels: {', '.join(off)} not a multiple of step {step:g}")
    return problems


def _on_step(value: float, step: float) -> bool:
    return abs(value / step - round(value / step)) < 1e-9


def _level(*extra: Field) -> Schema:
    return Schema("level", [
        field("value", float, minimum=LOWEST, maximum=HIGHEST),
        field("label", str, minimum=1, maximum=60),
        *extra,
    ])


SCALES_SCHEMA = Schema("[scales]", [
    field("morning_trace", dict, schema=Schema("[scales.morning_trace]", [
        field("levels", list, minimum=2, maximum=11, item=field("level", dict, schema=_level(
            field("slug", str, pattern=SLUG),
            field("role", str, choices=[role.value for role in TraceRole]),
        ))),
    ], check=_trace_check)),
    field("effort_pain", dict, schema=Schema("[scales.effort_pain]", [
        field("step", float, minimum=0.1, maximum=1,
              note="the smallest move a reading may make, e.g. 0.5 for half points"),
        field("levels", list, minimum=2, maximum=11, item=field("level", dict, schema=_level(
            field("role", str, required=False,
                  choices=[role.value for role in EffortRole]),
        ))),
    ], check=_effort_check)),
])


@dataclass(frozen=True)
class MorningTraceScale:
    levels: tuple[Level, ...]

    @property
    def base(self) -> Level:
        return next(level for level in self.levels if level.role == TraceRole.BASE)

    def level(self, slug: str) -> Level | None:
        return next((level for level in self.levels if level.slug == slug), None)

    def said(self, value: float) -> str:
        """The label of the level `value` records, or the bare value off the scale."""
        return next((level.label for level in self.levels if level.value == value),
                    f"{value:g}")

    def reaches_base(self, value: float) -> bool:
        """At or above the athlete's usual morning."""
        return value >= self.base.value

    def above_base(self, value: float) -> bool:
        return value > self.base.value

    def below_base(self, value: float) -> bool:
        return value < self.base.value

    @property
    def smallest_step(self) -> float:
        """The smallest gap between two neighbouring levels."""
        values = [level.value for level in self.levels]
        return min(later - earlier for earlier, later in pairwise(values))


@dataclass(frozen=True)
class EffortPainScale:
    levels: tuple[Level, ...]
    step: float

    def _role(self, role: EffortRole) -> float:
        return next(level.value for level in self.levels if level.role == role)

    @property
    def cap(self) -> float:
        return self._role(EffortRole.CAP)

    @property
    def severe(self) -> float:
        return self._role(EffortRole.SEVERE)

    @property
    def lowest(self) -> float:
        return self.levels[0].value

    @property
    def highest(self) -> float:
        return self.levels[-1].value

    def above_cap(self, value: float) -> bool:
        return value > self.cap

    def within(self, value: float) -> bool:
        return self.lowest <= value <= self.highest

    def on_step(self, value: float) -> bool:
        return _on_step(value, self.step)


@dataclass(frozen=True)
class PainScales:
    morning_trace: MorningTraceScale
    effort_pain: EffortPainScale

    def spoken(self) -> dict[str, str]:
        """What a rule's text or a prompt may cite: the cap and severe levels, the morning
        levels and their slugs."""
        def said(value: float) -> str:
            return f"{value:g}".replace(".", ",")
        trace = self.morning_trace
        return {
            "pain_cap": said(self.effort_pain.cap),
            "pain_severe": said(self.effort_pain.severe),
            "wake_base": trace.base.label,
            "wake_levels": ", ".join(level.label for level in trace.levels),
            "wake_slugs": ", ".join(f"`{level.slug}`" for level in trace.levels),
        }


def build(raw: dict) -> PainScales:
    """The scales of a `[scales]` table already validated by `SCALES_SCHEMA`."""
    def levels(entries: list[dict]) -> tuple[Level, ...]:
        return tuple(Level(value=float(entry["value"]), label=entry["label"],
                           role=entry.get("role"), slug=entry.get("slug"))
                     for entry in entries)
    trace, effort = raw["morning_trace"], raw["effort_pain"]
    return PainScales(MorningTraceScale(levels(trace["levels"])),
                      EffortPainScale(levels(effort["levels"]), float(effort["step"])))
