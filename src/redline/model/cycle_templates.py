"""Cycle templates: the method each kind of training cycle follows, version 2.

One template sort per `CycleKind`, each a top-level table list of `cycle_templates.toml`:
- `[[preparation]]`, by race distance and terrain: the window before the goal, its
  phases, their key sessions from a starting dose to an arrival dose, the taper, the
  deloads;
- `[[general]]`, by level: the general preparation between goals, in blocks, its volume
  band, its key session, its deloads triggered by signals;
- `[[recovery]]`, by race distance: the weeks after a race;
- `[[rebuild]]`: the way back after a break, from a floor.

A template is a method: it holds no athlete value. Volumes are shares of the highest
weekly volume the athlete held; intensities are bands around a reference (`IntensityRef`),
a speed reference in % of its speed, never of its pace. An instance declares its own
`cycle_templates.toml`, which replaces the engine's as a whole; `version` says which
format it follows.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import formats, session_kinds
from .errors import ConfigError
from .progression import RUN_FAMILIES, Dose
from .progression import default as default_families
from .schema import Field, Schema, SchemaError, field
from .vocabulary import (
    SPEED_REFS,
    CycleKind,
    DeloadMode,
    GeneralLevel,
    IntensityRef,
    PlanPhase,
    RaceTerrain,
)

INSTANCE_NAME = "cycle_templates.toml"
VERSION = 2
#: The phases of a preparation's window, in the order they run; the taper ends it.
PREPARATION_PHASES = (PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC, PlanPhase.TAPER)
#: The workouts a key session may be: quality sessions made of repetitions.
KEY_WORKOUTS = tuple(sorted(session_kinds.QUALITY & session_kinds.REPETITIONS))
#: The bounds a band may take, by reference: % of a speed, the 1-10 effort scale, zones.
BAND_BOUNDS: dict[IntensityRef, tuple[float, float]] = {
    **dict.fromkeys(SPEED_REFS, (50.0, 130.0)),
    IntensityRef.RPE: (1.0, 10.0),
    IntensityRef.HR_ZONE: (1.0, 5.0),
}


class TemplateError(ConfigError):
    """Unreadable or non-conforming cycle templates."""


# ------------------------------------------------------------------ the declaration


def _dose_variables() -> tuple[str, ...]:
    """The variables a key session's dose may name: the ladders of the run families."""
    families = default_families()
    return tuple(dict.fromkeys(variable.name for slug in RUN_FAMILIES
                               for variable in families[slug].ladder))


def _range(name: str, kind: type, low: float, high: float, note: str = "",
           required: bool = True) -> Field:
    return field(name, list, required=required, minimum=2, maximum=2,
                 item=field(name, kind, minimum=low, maximum=high), note=note)


def _ordered(*names: str) -> Callable[[dict], list[str]]:
    """The check that each named [minimum, maximum] range is in order."""
    def check(data: dict) -> list[str]:
        return [f"{name} {data[name]} is not a [minimum, maximum] range" for name in names
                if data.get(name) is not None and data[name][0] > data[name][1]]
    return check


def _all(*checks: Callable[[dict], list[str]]) -> Callable[[dict], list[str]]:
    return lambda data: [problem for check in checks for problem in check(data)]


def _band_check(data: dict) -> list[str]:
    low, high = BAND_BOUNDS[IntensityRef(data["ref"])]
    band = data["band"]
    problems = []
    if not all(low <= value <= high for value in band):
        problems.append(f"band {band}: from {low:g} to {high:g} for {data['ref']}")
    if band[0] > band[1]:
        problems.append(f"band {band} is not a [minimum, maximum] range")
    return problems


INTENSITY = Schema("intensity", [
    field("ref", str, choices=tuple(IntensityRef)),
    _range("band", float, 0, 1000,
           note="% of the speed for a speed reference, the 1-10 scale for rpe, zones"),
], check=_band_check)

SEGMENTS = Schema("long_run_segments", [
    field("ref", str, choices=tuple(IntensityRef)),
    _range("band", float, 0, 1000),
    _range("share", float, 0, 1, note="the share of the long run run at this intensity"),
], check=_all(_band_check, _ordered("share")))


def _dose_schema(name: str) -> Schema:
    return Schema(name, [field(variable, float, required=False, minimum=0)
                         for variable in _dose_variables()],
                  check=lambda data: [] if data else ["a dose names one variable at least"])


def _key_check(data: dict) -> list[str]:
    if set(data["start"]) != set(data["arrival"]):
        return [f"{data['workout']}: start and arrival name different variables "
                f"({', '.join(sorted(data['start']))} / {', '.join(sorted(data['arrival']))})"]
    return []


KEY = Schema("key", [
    field("workout", str, choices=KEY_WORKOUTS),
    field("intensity", dict, schema=INTENSITY),
    field("start", dict, schema=_dose_schema("start")),
    field("arrival", dict, schema=_dose_schema("arrival")),
    field("flagship", int, required=False, minimum=0, maximum=6,
          note="the last exposures that are flagship sessions"),
], check=_key_check)


def _qualities_check(data: dict) -> list[str]:
    keys = data.get("key") or []
    if data["qualities"] and not keys:
        return [f"{data['qualities']} quality session(s) a week, and no key session"]
    if not data["qualities"] and keys:
        return ["key sessions without a quality session a week"]
    return []


def _phase_schema(phase: PlanPhase) -> Schema:
    ranged = phase != PlanPhase.TAPER
    return Schema(f"[preparation.{phase}]", [
        *([_range("weeks", int, 0, 20)] if ranged else []),
        field("qualities", int, minimum=0, maximum=4),
        field("key", list, required=False, maximum=4, item=field("key", dict, schema=KEY)),
        field("long_run_segments", dict, required=False, schema=SEGMENTS),
    ], check=_all(_ordered("weeks"), _qualities_check))


DELOAD_FIELDS = (
    field("deload_mode", str, choices=tuple(DeloadMode)),
    _range("deload_every_weeks", int, 2, 8, required=False,
           note="a scheduled deload comes every N weeks"),
    _range("deload_days", int, 1, 7, required=False, note="a triggered deload lasts N days"),
    field("deload_cut_pct", int, required=False, minimum=10, maximum=60),
)
#: What each deload mode needs; the fields of the other mode do not apply to it.
DELOAD_NEEDS = {DeloadMode.SCHEDULED: ("deload_every_weeks", "deload_cut_pct"),
                DeloadMode.TRIGGERED: ("deload_days",)}


def _deload_check(data: dict) -> list[str]:
    mode = DeloadMode(data["deload_mode"])
    others = {name for other, names in DELOAD_NEEDS.items() if other != mode for name in names}
    return ([f"a {mode} deload needs {name}" for name in DELOAD_NEEDS[mode]
             if data.get(name) is None]
            + [f"{name} does not apply to a {mode} deload" for name in sorted(others)
               if data.get(name) is not None])


def _preparation_check(data: dict) -> list[str]:
    phases = [PlanPhase(name) for name in data["phases"]]
    problems = []
    if phases != sorted(set(phases), key=PREPARATION_PHASES.index) or phases[-1:] != [
            PlanPhase.TAPER]:
        problems.append(f"phases {data['phases']}: each once, in the order "
                        f"{', '.join(PREPARATION_PHASES)}, the taper last")
    declared = [phase for phase in PREPARATION_PHASES if data.get(str(phase)) is not None]
    problems += [f"phase {phase} is listed without its [preparation.{phase}] table"
                 for phase in phases if phase not in declared]
    problems += [f"[preparation.{phase}] is not a listed phase"
                 for phase in declared if phase not in phases]
    shortest = data["taper_weeks"] + sum(data[str(phase)]["weeks"][0] for phase in phases
                                         if phase != PlanPhase.TAPER and phase in declared)
    if shortest > data["window_weeks"][1]:
        problems.append(f"the phases last {shortest} weeks at least, beyond the window's "
                        f"{data['window_weeks'][1]}")
    return problems


PREPARATION = Schema("[[preparation]]", [
    field("slug", str, pattern=formats.SLUG),
    field("terrain", str, required=False, choices=tuple(RaceTerrain),
          note="absent, the template serves either terrain"),
    field("distance_km_max", float, minimum=1, maximum=400,
          note="the longest race the template serves"),
    field("taper_weeks", int, minimum=1, maximum=6, note="the race week included"),
    _range("taper_cut_pct", int, 0, 100, note="the taper's mean volume drop"),
    *DELOAD_FIELDS,
    field("phases", list, minimum=1, maximum=len(PREPARATION_PHASES),
          item=field("phase", str, choices=tuple(PREPARATION_PHASES))),
    _range("window_weeks", int, 1, 40, note="the phases from the first to the race week"),
    field("min_held_km", float, minimum=0,
          note="under this highest weekly volume held, the template is out of its domain"),
    *(field(str(phase), dict, required=False, schema=_phase_schema(phase))
      for phase in PREPARATION_PHASES),
], check=_all(_ordered("taper_cut_pct", "window_weeks", "deload_every_weeks", "deload_days"),
              _deload_check, _preparation_check))


def _general_check(data: dict) -> list[str]:
    progress = data["level"] == GeneralLevel.PROGRESS
    if progress and data.get("ramp_max_pct") is None:
        return ["the progress level needs ramp_max_pct"]
    if not progress and data.get("ramp_max_pct") is not None:
        return [f"ramp_max_pct belongs to the progress level, not {data['level']}"]
    return []


GENERAL = Schema("[[general]]", [
    field("level", str, choices=tuple(GeneralLevel)),
    *DELOAD_FIELDS,
    field("block_weeks", int, minimum=1, maximum=12,
          note="a version covers one block; the next block is a new version"),
    _range("volume_pct", float, 0, 150, note="% of the highest weekly volume held"),
    field("band_from_km", float, minimum=0,
          note="under this highest weekly volume held, the band is the volume held"),
    field("qualities", int, minimum=0, maximum=2),
    field("long_run_share_max", float, minimum=0, maximum=1),
    field("min_held_km", float, minimum=0),
    field("ramp_max_pct", float, required=False, minimum=0, maximum=20),
    field("key", list, required=False, maximum=2, item=field("key", dict, schema=KEY)),
], check=_all(_ordered("volume_pct", "deload_every_weeks", "deload_days"), _deload_check,
              _qualities_check, _general_check))

RECOVERY = Schema("[[recovery]]", [
    field("distance_km_max", float, minimum=1, maximum=400,
          note="the longest race this recovery follows"),
    _range("weeks", int, 0, 8),
    field("no_quality_days", int, minimum=0, maximum=28),
    _range("volume_pct", float, 0, 150, note="% of the highest weekly volume held"),
], check=_ordered("weeks", "volume_pct"))

REBUILD = Schema("[[rebuild]]", [
    field("floor_pct", float, minimum=0, maximum=100,
          note="% of the highest weekly volume held, where the way back starts"),
    field("ramp_max_pct", float, minimum=0, maximum=20),
])

SORTS: dict[CycleKind, Schema] = {CycleKind.PREPARATION: PREPARATION,
                                  CycleKind.GENERAL: GENERAL, CycleKind.RECOVERY: RECOVERY,
                                  CycleKind.REBUILD: REBUILD}

DECLARATION = Schema(INSTANCE_NAME, [
    field("version", int),
    field("default_level", str, choices=tuple(GeneralLevel)),
    *(field(str(kind), list, minimum=1, maximum=1 if kind == CycleKind.REBUILD else 20,
            item=field(str(kind), dict, schema=schema)) for kind, schema in SORTS.items()),
])


# ------------------------------------------------------------------ the objects


@dataclass(frozen=True)
class Range:
    low: float
    high: float

    @classmethod
    def of(cls, values: Sequence[float]) -> Range:
        return cls(float(values[0]), float(values[1]))


@dataclass(frozen=True)
class Intensity:
    ref: IntensityRef
    #: % of the reference's speed, the 1-10 scale for rpe, zones for hr_zone.
    band: Range


@dataclass(frozen=True)
class KeySession:
    workout: str
    intensity: Intensity
    start: Dose
    arrival: Dose
    #: The last exposures that are flagship sessions.
    flagship: int = 0


@dataclass(frozen=True)
class Segments:
    """The share of the long run run at an intensity (goal pace, in a specific block)."""

    intensity: Intensity
    share: Range


@dataclass(frozen=True)
class Phase:
    name: PlanPhase
    #: The phase's length; the taper's is the template's `taper_weeks`.
    weeks: Range
    qualities: int
    keys: tuple[KeySession, ...] = ()
    segments: Segments | None = None


@dataclass(frozen=True)
class Deload:
    mode: DeloadMode
    every_weeks: Range | None = None
    days: Range | None = None
    cut_pct: int | None = None


@dataclass(frozen=True)
class Preparation:
    slug: str
    terrain: RaceTerrain | None
    distance_km_max: float
    taper_weeks: int
    taper_cut_pct: Range
    deload: Deload
    phases: tuple[Phase, ...]
    window_weeks: Range
    min_held_km: float


@dataclass(frozen=True)
class General:
    level: GeneralLevel
    deload: Deload
    block_weeks: int
    volume_pct: Range
    band_from_km: float
    qualities: int
    long_run_share_max: float
    min_held_km: float
    keys: tuple[KeySession, ...] = ()
    ramp_max_pct: float | None = None


@dataclass(frozen=True)
class Recovery:
    distance_km_max: float
    weeks: Range
    no_quality_days: int
    volume_pct: Range


@dataclass(frozen=True)
class Rebuild:
    floor_pct: float
    ramp_max_pct: float


@dataclass(frozen=True)
class CycleTemplates:
    preparation: tuple[Preparation, ...]
    general: tuple[General, ...]
    recovery: tuple[Recovery, ...]
    rebuild: Rebuild
    default_level: GeneralLevel

    def general_at(self, level: GeneralLevel) -> General | None:
        return next((template for template in self.general if template.level == level), None)


def _intensity(data: dict) -> Intensity:
    return Intensity(IntensityRef(data["ref"]), Range.of(data["band"]))


def _dose(data: dict) -> Dose:
    return Dose(tuple((name, float(data[name])) for name in _dose_variables() if name in data))


def _keys(data: dict) -> tuple[KeySession, ...]:
    return tuple(KeySession(key["workout"], _intensity(key["intensity"]), _dose(key["start"]),
                            _dose(key["arrival"]), key.get("flagship") or 0)
                 for key in data.get("key") or [])


def _deload(data: dict) -> Deload:
    every, days = data.get("deload_every_weeks"), data.get("deload_days")
    return Deload(DeloadMode(data["deload_mode"]), Range.of(every) if every else None,
                  Range.of(days) if days else None, data.get("deload_cut_pct"))


def _preparation(data: dict) -> Preparation:
    phases = []
    for name in data["phases"]:
        raw, phase = data[name], PlanPhase(name)
        segments = raw.get("long_run_segments")
        weeks = raw.get("weeks", [data["taper_weeks"], data["taper_weeks"]])
        phases.append(Phase(phase, Range.of(weeks), raw["qualities"], _keys(raw),
                            Segments(_intensity(segments), Range.of(segments["share"]))
                            if segments else None))
    terrain = data.get("terrain")
    return Preparation(data["slug"], RaceTerrain(terrain) if terrain else None,
                       float(data["distance_km_max"]), data["taper_weeks"],
                       Range.of(data["taper_cut_pct"]), _deload(data), tuple(phases),
                       Range.of(data["window_weeks"]), float(data["min_held_km"]))


def _general(data: dict) -> General:
    ramp = data.get("ramp_max_pct")
    return General(GeneralLevel(data["level"]), _deload(data), data["block_weeks"],
                   Range.of(data["volume_pct"]), float(data["band_from_km"]),
                   data["qualities"], float(data["long_run_share_max"]),
                   float(data["min_held_km"]), _keys(data),
                   None if ramp is None else float(ramp))


def _doubled(values: Sequence[object]) -> list[str]:
    return sorted({str(value) for value in values if values.count(value) > 1})


def parse(text: str, source: Path) -> CycleTemplates:
    """The templates of a declaration. Every problem is reported at once."""
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TemplateError(f"{source} unreadable: {exc}") from None
    if payload.get("version") != VERSION:
        raise TemplateError(f"{source}: version {payload.get('version')}; this engine reads "
                            f"version {VERSION} of the cycle templates: rewrite the file in "
                            f"version {VERSION}")
    try:
        DECLARATION.validate(payload, str(source))
    except SchemaError as exc:
        raise TemplateError(str(exc)) from None
    found = CycleTemplates(
        tuple(_preparation(item) for item in payload[CycleKind.PREPARATION]),
        tuple(_general(item) for item in payload[CycleKind.GENERAL]),
        tuple(Recovery(float(item["distance_km_max"]), Range.of(item["weeks"]),
                       item["no_quality_days"], Range.of(item["volume_pct"]))
              for item in payload[CycleKind.RECOVERY]),
        Rebuild(*(float(payload[CycleKind.REBUILD][0][name])
                  for name in ("floor_pct", "ramp_max_pct"))),
        GeneralLevel(payload["default_level"]))
    problems = []
    if doubled := _doubled([template.slug for template in found.preparation]):
        problems.append(f"duplicate preparation template: {', '.join(doubled)}")
    if doubled := _doubled([template.level for template in found.general]):
        problems.append(f"one general template per level: {', '.join(doubled)} twice")
    if doubled := _doubled([template.distance_km_max for template in found.recovery]):
        problems.append(f"two recovery templates for {', '.join(doubled)} km")
    if found.general_at(found.default_level) is None:
        problems.append(f"default_level {found.default_level} has no general template")
    if problems:
        raise TemplateError("\n  - ".join([f"{source}:", *problems]))
    return found


def load_instance(vault: Path) -> CycleTemplates | None:
    """The instance's cycle templates, or None when it declares none."""
    path = vault / INSTANCE_NAME
    return parse(path.read_text(encoding="utf-8"), path) if path.is_file() else None
