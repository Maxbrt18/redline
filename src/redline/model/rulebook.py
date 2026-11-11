"""The compiled rulebook: the guardrails IN FORCE, one line each, and their parameters.

Guardrails must not live in plan prose interleaved with their history, or the model has
to dig the current version out of a palimpsest. The rulebook keeps the two apart:

- **the rule in force**: short, parameterised, graded, with its owner;
- **its reasoning and history**: stay with the named owner.

The rulebook belongs to the instance: `rulebook.toml` at the vault root. Without it the
engine applies `rulebook.default.toml`, its default. One replaces the other entirely: a
partial merge would let two versions of the same rule coexist, the very fault this module
exists to prevent.

It is the SINGLE SOURCE of the thresholds the code applies (`redline ledger`,
`redline feasibility`, `transitions.py`) and of the transition kinds a program may
carry. A threshold cited in a rule's text (`{pain_cap}`) is rendered from the same value:
what the coach reads is what the code applies.

It also declares the instance's pain scales (`[scales]`, `redline.model.pain_scales`):
the rules compare a pain with the roles of its own scale, never with a number of the
engine's.

And the strength lineages the progression engine follows (`[progression]`,
`redline.model.progression`): one per exercise sheet, with the range and step of each
variable. Those numbers constrain a method, so they live here, never in a sheet.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import string
import tomllib
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import lru_cache
from pathlib import Path

from redline.coach.fr import rulebook as text

from . import formats, iso_week, paths
from .errors import ConfigError
from .pain_scales import SCALES_SCHEMA, PainScales
from .pain_scales import build as build_scales
from .progression import DeclarationError, Lineages, parse_lineages
from .schema import Schema, SchemaError, field

FILENAME = "rulebook.toml"
DEFAULT = Path(__file__).with_name("rulebook.default.toml")

#: Rules the engine enforces itself: the coach must read them, otherwise the code would
#: reject from the program what the injected rulebook does not state.
ENFORCED = ("n2", "n3", "n4", "n7", "n8", "po", "s9")

RULEBOOK_SCHEMA = Schema("rulebook.toml", [
    field("parameters", dict, schema=Schema("[parameters]", [
        field("transition_lookback_days", int, minimum=7, maximum=90),
        field("transitions_per_week", int, minimum=0, maximum=7),
        field("gate_effort_peak", float, minimum=0, maximum=10,
              note="on the effort-pain scale, at or below its cap"),
        field("gate_trend_weeks", int, minimum=1, maximum=8),
        field("hard_min_hours", float, minimum=0.25, maximum=6),
        field("taper_weeks", int, minimum=1, maximum=6),
        field("stall_weeks", int, minimum=1, maximum=12),
        field("stall_tolerance_km", float, minimum=0, maximum=5),
        field("wake_trace_until", str, pattern=formats.TIME,
              note="local time HH:MM"),
        field("missed_after_hours", float, minimum=0.5, maximum=24,
              note="hours after its planned start a session still not done counts as missed"),
        field("missed_cutoff", str, pattern=formats.TIME,
              note="local time HH:MM after which a session without a start time is missed"),
        field("signal_window_days", int, minimum=7, maximum=90,
              note="days the daily signal looks back over"),
        field("low_desire_checkins", int, minimum=2, maximum=14,
              note="latest daily check-ins all at low desire that raise a signal"),
        field("missed_sessions_signal", int, minimum=1, maximum=14,
              note="sessions missed within the window that raise a signal"),
        field("gear_alert_pct", int, minimum=50, maximum=100,
              note="share of its inspection distance at which a pair raises an alert, in %"),
        field("assimilation_weeks", list, item=field("week", str, pattern=iso_week.PATTERN,
                                                     note="ISO week, e.g. 2030-W35")),
        # Optional: an instance rulebook written before them states no fuelling ceiling,
        # and none applies until it declares one.
        field("carbs_cap_g_h", int, required=False, minimum=30, maximum=150,
              note="highest fuelling target a session may carry, in g/h"),
        field("carbs_mix_above_g_h", int, required=False, minimum=30, maximum=150,
              note="above this target, the session names a glucose-fructose mix"),
        field("taper_cut_min_pct", int, required=False, minimum=0, maximum=100,
              note="least drop of the taper's mean weekly volume, in %"),
        field("taper_cut_max_pct", int, required=False, minimum=0, maximum=100,
              note="largest drop of the taper's mean weekly volume, in %"),
        # Optional: absent, a reorganisation keeps one run and two sessions a day, does
        # not compare its load with the plan's, and lets the long run go any day.
        field("runs_per_day", int, required=False, minimum=1, maximum=3,
              note="most runs a reorganisation puts on one day"),
        field("sessions_per_day", int, required=False, minimum=1, maximum=4,
              note="most sessions, runs and strength together, on one day"),
        field("reorganize_load_margin_pct", int, required=False, minimum=0, maximum=200,
              note="a reorganised week whose load over 3 days exceeds the plan's peak by "
                   "more than this (%) says so"),
        field("long_run_weekdays", list, required=False,
              item=field("weekday", int, minimum=1, maximum=7),
              note="ISO weekdays the long run stays on unless the athlete pins it elsewhere"),
        # Optional: absent, rules n3 and n4 stay with the coach alone. Present, the
        # engine refuses the revisions that break their verifiable part.
        field("persistent_wake_mornings", int, required=False, minimum=2, maximum=7,
              note="consecutive mornings at the base of the morning-trace scale or above: "
                   "pain persisting beyond 48 h"),
        field("pain_no_quality_days", int, required=False, minimum=1, maximum=42,
              note="days without quality from the morning persistence is established"),
        field("pain_cut_pct", int, required=False, minimum=0, maximum=100,
              note="cut of a week planned before persistence was established, in %"),
        field("poor_nights_streak", int, required=False, minimum=2, maximum=7,
              note="consecutive poor nights that remove quality"),
        field("alarm_no_quality_days", int, required=False, minimum=1, maximum=14,
              note="days without quality after poor nights or a high easy-run HR"),
        field("comparable_duration_pct", int, required=False, minimum=1, maximum=100,
              note="two sessions of an accumulating kind compare when their durations "
                   "differ by at most this share of the longer one, in %"),
        field("comparable_sessions", int, required=False, minimum=1, maximum=20,
              note="the most recent comparable sessions an accumulating one is read against"),
        # Optional: absent, the debrief of an easy or long run carries no gap of that
        # kind. Read on the kilometres of the run (`redline.model.debrief.split_gaps`).
        field("split_hr_margin_bpm", int, required=False, minimum=0, maximum=30,
              note="a kilometre whose mean HR exceeds the session's HR cap by more than "
                   "this, in bpm, is named"),
        field("split_fast_start_pct", float, required=False, minimum=1, maximum=30,
              note="a first kilometre faster than the median of the others by at least "
                   "this, in %, is a fast start"),
        field("split_fade_pct", float, required=False, minimum=1, maximum=30,
              note="a last third slower than the kilometres before by at least this, "
                   "in %, is a fade"),
        field("split_decoupling_pct", float, required=False, minimum=1, maximum=30,
              note="a speed-to-HR ratio that drops by at least this between the halves, "
                   "in %, is a decoupling"),
        field("split_min_km", int, required=False, minimum=3, maximum=20,
              note="kilometres a run needs before its start, fade or decoupling is read"),
    ])),
    field("scales", dict, schema=SCALES_SCHEMA),
    # Optional: absent, no lineage is followed. Its schema is derived from the ladders of
    # the progression families (`redline.model.progression`), which validate it.
    field("progression", dict, required=False,
          note="the strength lineages the progression engine follows"),
    field("transitions", list, minimum=1, maximum=12, item=field(
        "transition", dict, schema=Schema("[[transitions]]", [
            field("slug", str, pattern=formats.SLUG),
            field("label", str, minimum=1, maximum=40),
            field("race_first", bool, required=False),
            field("quality", bool, required=False),
        ]))),
    field("rules", list, minimum=1, maximum=20, item=field(
        "rule", dict, schema=Schema("[[rules]]", [
            field("id", str, pattern=r"[a-z0-9]{1,4}"),
            field("title", str, minimum=1, maximum=40),
            field("text", str, minimum=1, maximum=400),
            field("grade", str, minimum=1, maximum=120),
            field("owner", str, minimum=1, maximum=200),
            field("enforced_by", str, minimum=1, maximum=300),
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
    hard_min_hours: float
    taper_weeks: int
    stall_weeks: int
    stall_tolerance_km: float
    wake_trace_until: dt.time
    missed_after_hours: float
    missed_cutoff: dt.time
    signal_window_days: int
    low_desire_checkins: int
    missed_sessions_signal: int
    gear_alert_pct: int
    assimilation_weeks: tuple[str, ...]
    carbs_cap_g_h: int | None = None
    carbs_mix_above_g_h: int | None = None
    taper_cut_min_pct: int | None = None
    taper_cut_max_pct: int | None = None
    runs_per_day: int = 1
    sessions_per_day: int = 2
    reorganize_load_margin_pct: int | None = None
    long_run_weekdays: tuple[int, ...] = ()
    persistent_wake_mornings: int | None = None
    pain_no_quality_days: int | None = None
    pain_cut_pct: int | None = None
    poor_nights_streak: int | None = None
    alarm_no_quality_days: int | None = None
    comparable_duration_pct: int | None = None
    comparable_sessions: int | None = None
    split_hr_margin_bpm: int | None = None
    split_fast_start_pct: float | None = None
    split_fade_pct: float | None = None
    split_decoupling_pct: float | None = None
    split_min_km: int | None = None

    def spoken(self) -> dict[str, str]:
        """Thresholds as a rule's text states them: decimal comma."""
        def said(value: object) -> str:
            if isinstance(value, float):
                return f"{value:g}".replace(".", ",")
            if isinstance(value, dt.time):
                return value.strftime("%H:%M")
            return str(value)
        return {name: said(value) for name, value in vars(self).items()
                if value is not None and not isinstance(value, tuple)}


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
    scales: PainScales
    transitions: tuple[TransitionKind, ...]
    rules: tuple[Rule, ...]
    source: Path
    progression: Lineages = dataclass_field(default_factory=Lineages)

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

    def spoken(self) -> dict[str, str]:
        """Every value a rule's text or a reading note may cite, as the coach says it."""
        return _spoken(self.parameters, self.scales)

    def render(self) -> str:
        """The block injected to the coach: the rule in force, never its history."""
        lines = [text.BLOCK_OPEN]
        lines.extend("- " + rule.line() for rule in self.rules)
        lines.append(text.BLOCK_CLOSE)
        return "\n".join(lines)


def _spoken(parameters: Parameters, scales: PainScales) -> dict[str, str]:
    return {**parameters.spoken(), **scales.spoken()}


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def parse(text: str, source: Path) -> Rulebook:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulebookError(f"{source} unreadable: {exc}") from None
    try:
        RULEBOOK_SCHEMA.validate(payload, str(source))
    except SchemaError as exc:
        raise RulebookError(str(exc)) from None
    raw = payload["parameters"]
    parameters = Parameters(**{
        **raw, "assimilation_weeks": tuple(raw["assimilation_weeks"]),
        "long_run_weekdays": tuple(raw.get("long_run_weekdays") or ()),
        "wake_trace_until": dt.time.fromisoformat(raw["wake_trace_until"]),
        "missed_cutoff": dt.time.fromisoformat(raw["missed_cutoff"]),
        **{name: float(raw[name])
           for name in ("gate_effort_peak", "hard_min_hours", "stall_tolerance_km",
                        "missed_after_hours", "split_fast_start_pct", "split_fade_pct",
                        "split_decoupling_pct")
           if name in raw}})
    scales = build_scales(payload["scales"])
    problems = []
    progression = Lineages()
    try:
        progression = parse_lineages(payload.get("progression"), scales.effort_pain.cap)
    except DeclarationError as exc:
        problems.append(str(exc))
    if parameters.gate_effort_peak > scales.effort_pain.cap:
        problems.append(f"parameters.gate_effort_peak {parameters.gate_effort_peak:g} is above "
                        f"the effort-pain cap {scales.effort_pain.cap:g}")
    for what, values in (("transition", [entry["slug"] for entry in payload["transitions"]]),
                         ("rule", [entry["id"] for entry in payload["rules"]])):
        if doubled := _duplicates(values):
            problems.append(f"duplicate {what}: {', '.join(doubled)}")
    ids = {entry["id"] for entry in payload["rules"]}
    if missing := [rule for rule in ENFORCED if rule not in ids]:
        problems.append("rule enforced by the engine missing from the rulebook: "
                        + ", ".join(missing))
    spoken = _spoken(parameters, scales)
    rules = []
    for entry in payload["rules"]:
        cited = {name for label in ("text", "enforced_by")
                 for _, name, _, _ in string.Formatter().parse(entry[label]) if name}
        if unknown := sorted(cited - spoken.keys()):
            problems.append(f"rule {entry["id"]}: unknown threshold {", ".join(unknown)} (known: {", ".join(sorted(spoken))})")
            continue
        rules.append(Rule(**{**entry, "text": entry["text"].format_map(spoken),
                             "enforced_by": entry["enforced_by"].format_map(spoken)}))
    if problems:
        raise RulebookError("\n  - ".join([f"{source} :", *problems]))
    return Rulebook(
        parameters=parameters,
        scales=scales,
        transitions=tuple(TransitionKind(**entry) for entry in payload["transitions"]),
        rules=tuple(rules),
        source=source,
        progression=progression,
    )


def location(vault: Path) -> Path:
    """The instance rulebook if it exists, else the engine's."""
    own = vault / FILENAME
    return own if own.is_file() else DEFAULT


def digest(vault: Path) -> str:
    """Fingerprint of the rulebook file in force, instance or engine default."""
    return hashlib.sha256(location(vault).read_bytes()).hexdigest()[:16]


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
