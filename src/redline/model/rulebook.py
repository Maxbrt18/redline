"""The compiled rulebook: the guardrails IN FORCE, one line each, and their parameters.

Guardrails must not live in plan prose interleaved with their history, or the model has
to dig the current version out of a palimpsest. The rulebook keeps the two apart:

- **the rule in force**: short, parameterised, graded, with its owner;
- **its reasoning and history**: stay with the named owner.

The rulebook belongs to the instance: `rulebook.toml` at the vault root. Without it the
engine applies `rulebook.default.toml`, its default. One replaces the other entirely: a
partial merge would let two versions of the same rule coexist, the very fault this module
exists to prevent.

It is the SINGLE SOURCE of the thresholds the code applies (`scripts/ledger.py`,
`scripts/feasibility.py`, `transitions.py`) and of the transition kinds a programme may
carry. A threshold cited in a rule's text (`{pain_cap}`) is rendered from the same value:
what the coach reads is what the code applies.
"""

from __future__ import annotations

import datetime as dt
import string
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import paths
from .errors import ConfigError
from .schema import Schema, SchemaError, field

FILENAME = "rulebook.toml"
DEFAULT = Path(__file__).with_name("rulebook.default.toml")
SLUG = r"[a-z0-9][a-z0-9-]{0,39}"
WEEK = r"\d{4}-W(0[1-9]|[1-4]\d|5[0-3])"

#: Rules the engine enforces itself: the coach must read them, otherwise the code would
#: reject from the programme what the injected rulebook does not state.
ENFORCED = ("n2", "n7", "n8", "po", "s9")

RULEBOOK_SCHEMA = Schema("rulebook.toml", [
    field("parameters", dict, schema=Schema("[parameters]", [
        field("transition_lookback_days", int, minimum=7, maximum=90),
        field("transitions_per_week", int, minimum=0, maximum=7),
        field("gate_effort_peak", float, minimum=0, maximum=10),
        field("gate_trend_weeks", int, minimum=1, maximum=8),
        field("pain_cap", int, minimum=0, maximum=10),
        field("hard_min_hours", float, minimum=0.25, maximum=6),
        field("taper_weeks", int, minimum=1, maximum=6),
        field("stall_weeks", int, minimum=1, maximum=12),
        field("stall_tolerance_km", float, minimum=0, maximum=5),
        field("wake_trace_until", str, pattern=r"([01]\d|2[0-3]):[0-5]\d",
              note="heure locale HH:MM"),
        field("assimilation_weeks", list, item=field("semaine", str, pattern=WEEK,
                                                     note="semaine ISO, ex. 2026-W35")),
    ])),
    field("transitions", list, minimum=1, maximum=12, item=field(
        "transition", dict, schema=Schema("[[transitions]]", [
            field("slug", str, pattern=SLUG),
            field("label", str, minimum=1, maximum=40),
            field("race_first", bool, required=False),
            field("quality", bool, required=False),
        ]))),
    field("rules", list, minimum=1, maximum=20, item=field(
        "règle", dict, schema=Schema("[[rules]]", [
            field("id", str, pattern=r"[a-z0-9]{1,4}"),
            field("title", str, minimum=1, maximum=40),
            field("text", str, minimum=1, maximum=400),
            field("grade", str, minimum=1, maximum=120),
            field("owner", str, minimum=1, maximum=200),
            field("enforced_by", str, minimum=1, maximum=200),
        ]))),
])


class RulebookError(ConfigError):
    """Unreadable or non-conforming rulebook."""


@dataclass(frozen=True)
class Parameters:
    transition_lookback_days: int
    transitions_per_week: int
    gate_effort_peak: float
    gate_trend_weeks: int
    pain_cap: int
    hard_min_hours: float
    taper_weeks: int
    stall_weeks: int
    stall_tolerance_km: float
    wake_trace_until: dt.time
    assimilation_weeks: tuple[str, ...]

    def spoken(self) -> dict[str, str]:
        """Thresholds as a rule's text states them: decimal comma."""
        def said(value: object) -> str:
            if isinstance(value, float):
                return f"{value:g}".replace(".", ",")
            if isinstance(value, dt.time):
                return value.strftime("%H:%M")
            return str(value)
        return {name: said(value) for name, value in vars(self).items()
                if not isinstance(value, tuple)}


@dataclass(frozen=True)
class TransitionKind:
    slug: str
    label: str
    race_first: bool = False
    quality: bool = False


@dataclass(frozen=True)
class Rule:
    """A rule in force. `text` is what the coach applies; the rest locates it."""

    id: str
    title: str
    text: str
    grade: str
    owner: str
    enforced_by: str

    def line(self) -> str:
        return f"{self.id} {self.title} : {self.text} ({self.grade})"


@dataclass(frozen=True)
class Rulebook:
    parameters: Parameters
    transitions: tuple[TransitionKind, ...]
    rules: tuple[Rule, ...]
    source: Path

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(kind.slug for kind in self.transitions)

    @property
    def race_firsts(self) -> tuple[str, ...]:
        return tuple(kind.slug for kind in self.transitions if kind.race_first)

    @property
    def quality_kinds(self) -> frozenset[str]:
        return frozenset(kind.slug for kind in self.transitions if kind.quality)

    def label(self, slug: str) -> str:
        """The declared label; a slug since removed from the rulebook stays readable."""
        for kind in self.transitions:
            if kind.slug == slug:
                return kind.label
        return slug

    def render(self) -> str:
        """The block injected to the coach: the rule in force, never its history."""
        lines = ["[RÈGLEMENT EN VIGUEUR, invisible, ne pas réciter. Il fait foi sur toute "
                 "formulation plus ancienne ; le raisonnement vit chez le propriétaire.]"]
        lines.extend("- " + rule.line() for rule in self.rules)
        lines.append("[/RÈGLEMENT]")
        return "\n".join(lines)


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def parse(text: str, source: Path) -> Rulebook:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulebookError(f"{source} illisible : {exc}") from None
    try:
        RULEBOOK_SCHEMA.validate(payload, str(source))
    except SchemaError as exc:
        raise RulebookError(str(exc)) from None
    raw = payload["parameters"]
    parameters = Parameters(**{
        **raw, "assimilation_weeks": tuple(raw["assimilation_weeks"]),
        "wake_trace_until": dt.time.fromisoformat(raw["wake_trace_until"]),
        **{name: float(raw[name])
           for name in ("gate_effort_peak", "hard_min_hours", "stall_tolerance_km")}})
    problems = []
    for what, values in (("transition", [entry["slug"] for entry in payload["transitions"]]),
                         ("règle", [entry["id"] for entry in payload["rules"]])):
        if doubled := _duplicates(values):
            problems.append(f"{what} en double : {', '.join(doubled)}")
    ids = {entry["id"] for entry in payload["rules"]}
    if missing := [rule for rule in ENFORCED if rule not in ids]:
        problems.append("règle appliquée par le moteur absente du règlement : "
                        + ", ".join(missing))
    spoken = parameters.spoken()
    rules = []
    for entry in payload["rules"]:
        cited = {name for _, name, _, _ in string.Formatter().parse(entry["text"]) if name}
        if unknown := sorted(cited - spoken.keys()):
            problems.append(f"règle {entry['id']} : seuil inconnu {', '.join(unknown)} "
                            f"(connus : {', '.join(sorted(spoken))})")
            continue
        rules.append(Rule(**{**entry, "text": entry["text"].format_map(spoken)}))
    if problems:
        raise RulebookError("\n  - ".join([f"{source} :", *problems]))
    return Rulebook(
        parameters=parameters,
        transitions=tuple(TransitionKind(**entry) for entry in payload["transitions"]),
        rules=tuple(rules),
        source=source,
    )


def location(vault: Path) -> Path:
    """The instance rulebook if it exists, else the engine's."""
    own = vault / FILENAME
    return own if own.is_file() else DEFAULT


@lru_cache(maxsize=4)
def _load(path: Path, _mtime_ns: int) -> Rulebook:
    return parse(path.read_text(encoding="utf-8"), path)


def load(vault: Path) -> Rulebook:
    """The vault rulebook, reread if it changed: the service does not restart."""
    path = location(vault)
    return _load(path, path.stat().st_mtime_ns)


def current() -> Rulebook:
    """The rulebook of this process's vault (`VAULT_PATH`)."""
    return load(paths.vault_root())


def render() -> str:
    """The rulebook block in force, as sent with each packet."""
    return current().render()
