"""Ce qui distingue une instance d'une autre : `redline.toml`, à la racine du vault.

Le moteur ne connaît ni le prénom de l'athlète, ni la blessure qu'il surveille, ni
les capacités qu'il suit. Tout ce qui changerait d'un athlète à l'autre se lit ici,
une fois, et voyage typé jusqu'au code qui en a besoin. Un prompt, un écran ou une
règle qui écrirait l'une de ces valeurs en dur réintroduirait le moteur à un seul
athlète que ce fichier existe pour défaire.

Fermé par défaut, comme le reste des schémas du dépôt : un champ inconnu est une
erreur, et un `[…]` resté tel quel dans `redline.example.toml` aussi -- un gabarit à
moitié rempli finirait mot pour mot dans un message à l'athlète.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import hr_zones
from .errors import ConfigError
from .schema import Schema, SchemaError, field

FILENAME = "redline.toml"
EXAMPLE = "redline.example.toml"

#: Un gabarit non rempli : `[Prénom]`, `[ville]`… Jamais une valeur légitime ici.
PLACEHOLDER = re.compile(r"\[[^\]]*\]")
SLUG = r"[a-z0-9][a-z0-9-]{0,39}"

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
    ])),
    field("notebook", dict, schema=Schema("[notebook]", [
        field("place", str, minimum=1, maximum=80,
              note="le lieu de la météo des séances sans lieu déclaré"),
    ])),
    # Zéro, une ou plusieurs zones : un athlète sans blessure n'a rien à déclarer.
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
        ]))),
])


class InstanceError(ConfigError):
    """`redline.toml` absent, illisible, non conforme ou encore à remplir."""


@dataclass(frozen=True)
class Capability:
    slug: str
    label: str


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

    @property
    def hr_bounds(self) -> tuple[int, ...]:
        """Les FC où commencent Z2 à Z5. Le haut de la Z2 (`[1]`) plafonne les EF."""
        return hr_zones.bounds(self.hr_rest, self.hr_max)

    def capability_label(self, slug: str) -> str:
        """Le libellé déclaré, sinon celui que porte le slug."""
        for capability in self.capabilities:
            if capability.slug == slug:
                return capability.label
        return derived_label(slug)


def derived_label(slug: str) -> str:
    """`cap2-descente` → « Descente » : lisible même sans déclaration."""
    return slug.split("-", 1)[-1].capitalize()


def placeholders(payload: Any, where: str = "") -> list[str]:
    """Les valeurs encore au gabarit, nommées par leur chemin."""
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


def parse(text: str) -> Instance:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InstanceError(f"{FILENAME} illisible : {exc}") from None
    # Le gabarit d'abord : `hr_max = "[FC max]"` est un champ à remplir, pas une erreur
    # de type à déchiffrer.
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
    return Instance(
        athlete_name=athlete["name"],
        place=payload["notebook"]["place"],
        injuries=tuple(Injury(entry["slug"], entry["label"]) for entry in injuries),
        capabilities=tuple(Capability(entry["slug"], entry["label"])
                           for entry in payload["capabilities"]),
        hr_rest=athlete["hr_rest"],
        hr_max=athlete["hr_max"],
        timezone=_timezone(athlete["timezone"]),
    )


def own_names(vault: Path) -> frozenset[str]:
    """Les noms propres de l'instance qui ne désignent aucun tiers : prénom et lieu.

    Sans `redline.toml` lisible, aucun : un contrôle de vie privée ne s'assouplit pas
    sur une configuration qu'il ne peut pas lire.
    """
    try:
        loaded = load(vault)
    except InstanceError:
        return frozenset()
    return frozenset({loaded.athlete_name, loaded.place})


def load(vault: Path) -> Instance:
    """L'instance du vault ; une erreur qui dit quoi faire, jamais un défaut silencieux."""
    path = vault / FILENAME
    if not path.is_file():
        raise InstanceError(
            f"{path} absent : copie {EXAMPLE} du moteur à la racine du vault, puis remplis-le")
    return parse(path.read_text(encoding="utf-8"))
