"""Plan templates: the phases a preparation runs through, by race distance and terrain.

The engine declares generic templates (`plan_templates.default.toml`); an instance may
replace them as a whole with a `plan_templates.toml` at its vault root. A template says
how long each phase may last, how often a deload comes and how deep, how many quality
sessions a week of each phase carries at most and which quality workouts it may hold. It
holds no athlete value: peak volume, peak long run and the quality sessions a week are
the race's (`redline.model.goal`).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import formats, session_kinds
from .errors import ConfigError
from .schema import Schema, SchemaError, field
from .vocabulary import PlanPhase, RaceTerrain

DEFAULT = Path(__file__).with_name("plan_templates.default.toml")
INSTANCE_NAME = "plan_templates.toml"
#: The phases whose length is a range; the taper's is `taper_weeks`.
RANGED = (PlanPhase.RECOVERY, PlanPhase.BASE, PlanPhase.DEVELOPMENT, PlanPhase.SPECIFIC)
QUALITY_WORKOUTS = tuple(sorted(session_kinds.REPETITIONS))


def _range_check(data: dict) -> list[str]:
    weeks = data.get("weeks")
    if weeks is None:
        return []
    if len(weeks) != 2 or weeks[0] > weeks[1]:
        return [f"weeks {weeks} is not a [minimum, maximum] range"]
    return []


def _phase_schema(name: str, ranged: bool) -> Schema:
    return Schema(f"[templates.{name}]", [
        *([field("weeks", list, minimum=2, maximum=2,
                 item=field("weeks", int, minimum=0, maximum=20))] if ranged else []),
        field("qualities", int, minimum=0, maximum=4),
        field("families", list, maximum=len(QUALITY_WORKOUTS),
              item=field("workout", str, choices=QUALITY_WORKOUTS)),
    ], check=_range_check)


TEMPLATE_SCHEMA = Schema("[[templates]]", [
    field("slug", str, pattern=formats.SLUG),
    field("terrain", str, required=False, choices=tuple(RaceTerrain),
          note="absent, the template serves either terrain"),
    field("distance_km_max", float, minimum=1, maximum=400,
          note="the longest race the template serves, in km"),
    field("taper_weeks", int, minimum=1, maximum=6, note="the race week included"),
    field("deload_every", list, minimum=1, maximum=3,
          item=field("weeks", int, minimum=2, maximum=6)),
    field("deload_cut_pct", int, minimum=10, maximum=50),
    *(field(str(phase), dict, schema=_phase_schema(str(phase), phase in RANGED))
      for phase in PlanPhase),
])
DECLARATION_SCHEMA = Schema("plan_templates.toml", [
    field("templates", list, minimum=1, maximum=20,
          item=field("template", dict, schema=TEMPLATE_SCHEMA)),
])


class TemplateError(ConfigError):
    """Unreadable or non-conforming plan templates."""


@dataclass(frozen=True)
class Phase:
    minimum: int
    maximum: int
    qualities: int
    families: tuple[str, ...]


@dataclass(frozen=True)
class Template:
    slug: str
    terrain: RaceTerrain | None
    distance_km_max: float
    taper_weeks: int
    deload_every: tuple[int, ...]
    deload_cut_pct: int
    phases: tuple[tuple[PlanPhase, Phase], ...]

    def phase(self, name: PlanPhase) -> Phase:
        return dict(self.phases)[name]

    def serves(self, distance_km: float, terrain: RaceTerrain) -> bool:
        return distance_km <= self.distance_km_max and self.terrain in (None, terrain)


def _template(data: dict) -> Template:
    phases = []
    for phase in PlanPhase:
        raw = data[str(phase)]
        low, high = raw.get("weeks", [data["taper_weeks"], data["taper_weeks"]])
        phases.append((phase, Phase(low, high, raw["qualities"], tuple(raw["families"]))))
    terrain = data.get("terrain")
    return Template(data["slug"], RaceTerrain(terrain) if terrain else None,
                    float(data["distance_km_max"]), data["taper_weeks"],
                    tuple(data["deload_every"]), data["deload_cut_pct"], tuple(phases))


def parse(text: str, source: Path) -> tuple[Template, ...]:
    """The templates of a declaration, in order. Every problem is reported at once."""
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TemplateError(f"{source} unreadable: {exc}") from None
    try:
        DECLARATION_SCHEMA.validate(payload, str(source))
    except SchemaError as exc:
        raise TemplateError(str(exc)) from None
    templates = tuple(_template(item) for item in payload["templates"])
    slugs = [template.slug for template in templates]
    if doubled := sorted({slug for slug in slugs if slugs.count(slug) > 1}):
        raise TemplateError(f"{source}: duplicate template: {', '.join(doubled)}")
    return templates


@lru_cache(maxsize=4)
def _read(path: Path) -> tuple[Template, ...]:
    return parse(path.read_text(encoding="utf-8"), path)


def load(vault: Path | None = None) -> tuple[Template, ...]:
    """The instance's templates when it declares them, else the engine's."""
    instance = vault / INSTANCE_NAME if vault is not None else None
    return _read(instance if instance is not None and instance.is_file() else DEFAULT)


def for_race(templates: tuple[Template, ...], distance_km: float,
             terrain: RaceTerrain) -> Template | None:
    """The first template that serves the race, in declaration order."""
    return next((template for template in templates if template.serves(distance_km, terrain)),
                None)
