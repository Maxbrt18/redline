"""The validator: one schema declaration, executable, and only one.

Three requirements shape everything here:

  - **Every error, not the first one.** Fixing a note blind over six round trips is
    what makes a validator get abandoned.
  - **Messages in French naming the field and the value.** The athlete reads them in
    `vault-check.py` output, Claude reads them when a script refuses its input. A
    message like « champ « dose.measure » : "rep" inconnu (attendus : amrap,
    contacts, reps, time) » fixes itself; "ValidationError" does not.
  - **Strict by default.** An unknown field is an ERROR. A typo silently swallowed
    is the failure this layer exists to delete.

No dependencies: any script in the repo must import this with nothing installed.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from dataclasses import field as _dataclass_field
from typing import Any


class SchemaError(ValueError):
    """Non-conforming data. Carries EVERY problem, not the first one."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n  - ".join(["donnée invalide :", *self.problems]))


# How each accepted type is named to whoever reads the error.
TYPE_NAMES = {
    str: "texte",
    int: "entier",
    float: "nombre",
    bool: "booléen",
    list: "liste",
    dict: "objet",
    _dt.date: "date",
}
FEMININE = {"liste", "date"}   # « date reçue », but « entier reçu »


def _name_of(kind: type) -> str:
    return TYPE_NAMES.get(kind, getattr(kind, "__name__", str(kind)))


def _received(value: Any) -> str:
    name = _name_of(type(value))
    return f"{name} reçue" if name in FEMININE else f"{name} reçu"


def _expected(kind: type) -> str:
    name = _name_of(kind)
    return f"{name} attendue" if name in FEMININE else f"{name} attendu"


@dataclass(frozen=True)
class Field:
    """An expected field. Everything is optional but the name and the type."""

    name: str
    type: type = str
    required: bool = True
    choices: Sequence[Any] | None = None
    pattern: str | None = None          # str only, fullmatch
    minimum: float | None = None        # value for a number, length otherwise
    maximum: float | None = None
    item: Field | None = None         # element spec for a list
    schema: Schema | None = None      # nested object
    default: Any = None
    note: str = ""                         # reminder appended to the error


def field(name: str, type: type = str, **kwargs) -> Field:
    """Sugar: field("sets", int, minimum=1, maximum=12)."""
    return Field(name=name, type=type, **kwargs)


@dataclass
class Schema:
    """A set of fields, and the strict-by-default rule."""

    name: str
    fields: Sequence[Field]
    extra: bool = False          # True = tolerate unknown fields
    check: Callable[[dict], Sequence[str]] | None = None
    _by_name: dict = _dataclass_field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_name = {spec.name: spec for spec in self.fields}

    def errors(self, payload: Any, where: str = "") -> list:
        """Every problem, in French, prefixed by `where` (a file path or the
        name of the parent field)."""
        prefix = f"{where} : " if where else ""
        if not isinstance(payload, dict):
            return [f"{prefix}{self.name} : objet attendu, {_received(payload)}"]

        problems = []
        for name in payload:
            if name not in self._by_name and not self.extra:
                known = ", ".join(sorted(self._by_name))
                problems.append(f"{prefix}champ « {name} » inconnu (attendus : {known})")

        for spec in self.fields:
            if payload.get(spec.name) is None:
                if spec.required and spec.default is None:
                    problems.append(f"{prefix}champ « {spec.name} » manquant"
                                    + (f" — {spec.note}" if spec.note else ""))
                continue
            problems.extend(_field_errors(spec, payload[spec.name], prefix))

        # Cross-field consistency ("seconds is required when measure is time")
        # cannot be declared field by field, and only runs once the rest is
        # valid -- otherwise it piles errors derived from an error.
        if not problems and self.check is not None:
            problems.extend(f"{prefix}{problem}" for problem in self.check(payload))
        return problems

    def validate(self, payload: Any, where: str = "") -> dict:
        """The payload completed with its defaults, or SchemaError."""
        problems = self.errors(payload, where)
        if problems:
            raise SchemaError(problems)
        complete = dict(payload)
        for spec in self.fields:
            if complete.get(spec.name) is None and spec.default is not None:
                complete[spec.name] = spec.default
        return complete


def _field_errors(spec: Field, value: Any, prefix: str) -> list:
    name = f"{prefix}champ « {spec.name} »"

    # bool before int: in Python True IS an int, and "side: true" must be
    # refused rather than pass for 1.
    if spec.type is not bool and isinstance(value, bool):
        return [f"{name} : booléen reçu, {_expected(spec.type)}"]
    if spec.type is float and isinstance(value, int):
        value = float(value)
    if not isinstance(value, spec.type):
        return [f"{name} : {_expected(spec.type)}, {_received(value)} ({value!r})"]

    # Past this line the value IS of `spec.type` -- but `spec.type` is a variable, so
    # no checker can carry that. The rebinding states it instead of pretending.
    checked: Any = value
    problems = []
    if spec.choices is not None and value not in spec.choices:
        allowed = ", ".join(str(choice) for choice in sorted(spec.choices, key=str))
        problems.append(f"{name} : {value!r} inconnu (attendus : {allowed})")
    if spec.pattern is not None and not re.fullmatch(spec.pattern, checked):
        problems.append(f"{name} : {value!r} ne suit pas le format attendu"
                        + (f" — {spec.note}" if spec.note else f" ({spec.pattern})"))

    # minimum/maximum bound a VALUE for a number, a LENGTH for anything else.
    # The message says which, otherwise « champ « exercises » : 0, minimum 1 »
    # reads as a value.
    if spec.minimum is not None or spec.maximum is not None:
        sized = isinstance(checked, (list, dict, str))
        measured = len(checked) if sized else checked
        what = "longueur" if sized else "valeur"
        if spec.minimum is not None and measured < spec.minimum:
            problems.append(f"{name} : {what} {measured:g}, minimum {spec.minimum:g}")
        if spec.maximum is not None and measured > spec.maximum:
            problems.append(f"{name} : {what} {measured:g}, maximum {spec.maximum:g}")

    if isinstance(value, list) and spec.item is not None:
        for index, element in enumerate(value):
            element_spec = Field(
                name=f"{spec.name}[{index}]", type=spec.item.type,
                choices=spec.item.choices, pattern=spec.item.pattern,
                minimum=spec.item.minimum, maximum=spec.item.maximum,
                schema=spec.item.schema, note=spec.item.note)
            problems.extend(_field_errors(element_spec, element, prefix))
    if isinstance(value, dict) and spec.schema is not None:
        problems.extend(spec.schema.errors(value, f"{prefix}{spec.name}"))
    return problems
