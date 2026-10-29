"""What distinguishes one instance from another: `redline.toml`, at the vault root.

The engine knows neither the athlete's name, nor the injury being watched, nor the
capabilities being tracked. Everything that would vary from one athlete to another is
read here, once, and travels typed to the code that needs it. A prompt, screen or rule
that hardcoded one of these values would reintroduce the single-athlete engine this
file exists to undo.

Closed by default, like the repository's other schemas: an unknown field is an error,
and so is a `[…]` left as-is from `config/redline.example.toml` -- a half-filled template
would end up word for word in a message to the athlete.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from babel import Locale, UnknownLocaleError

from . import hr_zones
from .errors import ConfigError
from .schema import Schema, SchemaError, field
from .startup_budget import Tier, ceiling_errors

FILENAME = "redline.toml"
EXAMPLE = "config/redline.example.toml"

#: An unfilled template value (`[Prénom]`, `[ville]`...). Never a legitimate value here.
PLACEHOLDER = re.compile(r"\[[^\]]*\]")
SLUG = r"[a-z0-9][a-z0-9-]{0,39}"

#: The locale of dates and lists (`redline.coach.dates`) when `[athlete]` declares none.
DEFAULT_LOCALE = "fr_FR"
#: The languages the coach has texts in (`redline/coach/<language>/`).
LANGUAGES = ("fr",)

#: The pictograms the notebook draws for a capability (`PAS_ICONS` in `ui.js`).
CAPABILITY_ICONS = ("run", "descent", "food", "night", "pain", "mind")

INSTANCE_SCHEMA = Schema("redline.toml", [
    field("athlete", dict, schema=Schema("[athlete]", [
        field("name", str, minimum=1, maximum=40,
              note="le prénom que le coach emploie dans ses messages"),
        field("hr_rest", int, minimum=30, maximum=100,
              note="la FC de repos, en bpm : la base des zones Karvonen"),
        field("hr_max", int, minimum=120, maximum=230,
              note="la FC max, en bpm, mesurée ou estimée"),
        field("timezone", str, minimum=1, maximum=64,
              note="le fuseau IANA où vit l'athlète, ex. « Europe/Paris »"),
        field("locale", str, required=False, minimum=2, maximum=20,
              note="la locale des dates et des listes, ex. « fr_FR » ou « fr_CA »"),
    ])),
    field("notebook", dict, schema=Schema("[notebook]", [
        field("place", str, minimum=1, maximum=80,
              note="le lieu de la météo des séances sans lieu déclaré"),
    ])),
    # Zero or more zones: an athlete with no injury has nothing to declare.
    field("injuries", list, required=False, item=field(
        "zone", dict, schema=Schema("[[injuries]]", [
            field("slug", str, pattern=SLUG),
            field("label", str, minimum=1, maximum=40,
                  note="le nom affiché de la zone surveillée, ex. « Genou »"),
        ]))),
    field("capabilities", list, minimum=1, maximum=9, item=field(
        "capacité", dict, schema=Schema("[[capabilities]]", [
            field("slug", str, pattern=SLUG),
            field("label", str, minimum=1, maximum=30),
            field("icon", str, required=False, choices=CAPABILITY_ICONS,
                  note="le pictogramme de la capacité dans le carnet"),
        ]))),
    field("ledger", dict, required=False, schema=Schema("[ledger]", [
        field("complete_from", str, required=False, pattern=r"\d{4}-\d{2}-\d{2}",
              note="le premier jour dont les séances doivent être complètes ; les "
                   "précédentes ne sont jamais réclamées"),
    ])),
    # What `redline vault-check` holds the vault to; without it, nothing is checked for it.
    field("vault", dict, required=False, schema=Schema("[vault]", [
        field("note_types", list, required=False, item=field(
            "type", str, minimum=1, maximum=40),
              note="les valeurs admises du champ `type` d'une note"),
        field("budget", list, required=False, item=field(
            "étage", dict, schema=Schema("[[vault.budget]]", [
                field("tier", str, minimum=1, maximum=40,
                      note="quand ces fichiers se paient, ex. « par tour »"),
                field("total", int, minimum=1, maximum=100_000),
                field("files", list, minimum=1, item=field(
                    "fichier", dict, schema=Schema("files", [
                        field("path", str, minimum=1, maximum=200),
                        field("words", int, minimum=1, maximum=100_000),
                    ]))),
            ]))),
    ])),
])


class InstanceError(ConfigError):
    """`redline.toml` is missing, unreadable, non-conforming or still to be filled in."""


@dataclass(frozen=True)
class Capability:
    slug: str
    label: str
    icon: str = "run"


@dataclass(frozen=True)
class Injury:
    slug: str
    label: str


@dataclass(frozen=True)
class Instance:
    athlete_name: str
    place: str
    injuries: tuple[Injury, ...]
    capabilities: tuple[Capability, ...]
    hr_rest: int
    hr_max: int
    timezone: ZoneInfo
    #: None when the instance declares no note types.
    note_types: frozenset[str] | None = None
    read_budget: tuple[Tier, ...] = ()
    locale: str = DEFAULT_LOCALE
    #: Observations before this day were written under a less demanding contract.
    complete_from: dt.date = dt.date.min

    @property
    def hr_bounds(self) -> tuple[int, ...]:
        """The heart rates where Z2 to Z5 start. The top of Z2 (`[1]`) caps easy runs."""
        return hr_zones.bounds(self.hr_rest, self.hr_max)

    def capability_label(self, slug: str) -> str:
        """The declared label, otherwise the one derived from the slug."""
        for capability in self.capabilities:
            if capability.slug == slug:
                return capability.label
        return derived_label(slug)

    def capability_icon(self, slug: str) -> str:
        for capability in self.capabilities:
            if capability.slug == slug:
                return capability.icon
        return "run"


def derived_label(slug: str) -> str:
    """`cap2-descente` -> `Descente`: readable even without a declaration."""
    return slug.split("-", 1)[-1].capitalize()


def placeholders(payload: Any, where: str = "") -> list[str]:
    """The values still at their template placeholder, named by path."""
    if isinstance(payload, dict):
        return [problem for key, value in payload.items()
                for problem in placeholders(value, f"{where}.{key}" if where else key)]
    if isinstance(payload, list):
        return [problem for index, value in enumerate(payload)
                for problem in placeholders(value, f"{where}[{index}]")]
    if isinstance(payload, str) and PLACEHOLDER.search(payload):
        return [f"champ « {where} » : {payload!r} est encore à remplir"]
    return []


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise InstanceError(f"{FILENAME} : fuseau inconnu « {name} » (nom IANA attendu, "
                            "ex. « Europe/Paris »)") from None


def _locale(name: str) -> str:
    try:
        language = Locale.parse(name).language
    except (UnknownLocaleError, ValueError):
        raise InstanceError(f"{FILENAME} : locale inconnue « {name} » (ex. « fr_FR »)") from None
    if language not in LANGUAGES:
        raise InstanceError(f"{FILENAME} : locale « {name} » sans textes du coach "
                            f"(langues disponibles : {', '.join(LANGUAGES)})")
    return name


def parse(text: str) -> Instance:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InstanceError(f"{FILENAME} illisible : {exc}") from None
    # Placeholders first: `hr_max = "[FC max]"` is a field to fill in, not a type error
    # to decipher.
    unfilled = placeholders(payload)
    if unfilled:
        raise InstanceError("\n  - ".join([f"{FILENAME} incomplet :", *unfilled]))
    try:
        INSTANCE_SCHEMA.validate(payload)
    except SchemaError as exc:
        raise InstanceError(str(exc)) from None
    athlete = payload["athlete"]
    slugs = [entry["slug"] for entry in payload["capabilities"]]
    if len(set(slugs)) != len(slugs):
        raise InstanceError(f"{FILENAME} : slug de capacité en double ({', '.join(slugs)})")
    injuries = payload.get("injuries", [])
    zones = [entry["slug"] for entry in injuries]
    if len(set(zones)) != len(zones):
        raise InstanceError(f"{FILENAME} : zone surveillée en double ({', '.join(zones)})")
    vault = payload.get("vault", {})
    ledger = payload.get("ledger", {})
    budget = tuple(Tier(tier["tier"], tier["total"],
                        tuple((entry["path"], entry["words"]) for entry in tier["files"]))
                   for tier in vault.get("budget", []))
    problems = ceiling_errors(budget)
    if problems:
        raise InstanceError("\n  - ".join([f"{FILENAME} : budget de lecture incohérent :",
                                             *problems]))
    return Instance(
        athlete_name=athlete["name"],
        place=payload["notebook"]["place"],
        injuries=tuple(Injury(entry["slug"], entry["label"]) for entry in injuries),
        capabilities=tuple(Capability(entry["slug"], entry["label"], entry.get("icon", "run"))
                           for entry in payload["capabilities"]),
        hr_rest=athlete["hr_rest"],
        hr_max=athlete["hr_max"],
        timezone=_timezone(athlete["timezone"]),
        note_types=frozenset(vault["note_types"]) if "note_types" in vault else None,
        read_budget=budget,
        locale=_locale(athlete.get("locale", DEFAULT_LOCALE)),
        complete_from=(dt.date.fromisoformat(ledger["complete_from"])
                       if "complete_from" in ledger else dt.date.min),
    )


def load(vault: Path) -> Instance:
    """The vault's instance; an error that says what to do, never a silent default."""
    path = vault / FILENAME
    if not path.is_file():
        raise InstanceError(
            f"{path} absent : copie {EXAMPLE} du moteur à la racine du vault, puis remplis-le")
    return parse(path.read_text(encoding="utf-8"))
