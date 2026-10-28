"""The dose algebra -- the type that replaces "3 x 8 / jambe" everywhere.

A dose is a small closed language: sets, a measure (reps, duration, to technical
failure, ground contacts) and a side. Everything `coaching/strength.md` prescribes
fits in it without remainder. Two choices worth stating:

  - **A range is a prescription, not sloppiness.** "8-10 reps", "30-45 s" are
    written that way by the coach and must stay that way. Hence `Span`, which reads
    either a scalar (`reps: 8`) or a pair (`reps: [8, 10]`) -- never a string.
  - **The frontmatter keys ARE these field names.** No translation table between the
    note and the code, because that table would be a second copy of the fact.

`human()` is the only place in the repo that builds a readable dose, and nothing
ever reads one back. No dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, TypeVar

from .schema import Schema, field

MEASURES = ("reps", "time", "amrap", "contacts")
SIDES = ("each", "both")

# Which field each measure cannot do without. Enforced by _consistency() at the
# schema boundary, and re-stated by _required() wherever the value is dereferenced.
REQUIRED_BY_MEASURE = {"reps": "reps", "time": "seconds", "contacts": "contacts"}

_T = TypeVar("_T")


def _required(value: _T | None, measure: str, name: str) -> _T:
    """The value the measure guarantees.

    A `Dose` built past the schema could carry `measure="time"` with no `seconds`.
    Saying so here turns what would be an AttributeError deep in a format string
    into a named refusal -- and lets the type checker see the invariant.
    """
    if value is None:
        raise ValueError(f"dose « {measure} » : champ « {name} » absent")
    return value

# The session vocabulary, in one place: this is what the athlete reads on the card
# and on the page.
FATIGUE = "jusqu'à échec technique"
PER_SIDE = " / jambe"

# Every field that accepts a scalar OR a [low, high] pair, with its ceiling.
# `sets` is in here because the programme really prescribes ranges of sets --
# "2-3× jusqu'à échec technique" (strength.md, exercice 3). Modelling that as a
# plain int would force the vault to round a prescription to fit the code.
BOUNDS = {"sets": 12, "reps": 200, "seconds": 3600}


@dataclass(frozen=True)
class Span:
    """A value, or a range. `high` is None when the value is exact."""

    low: int
    high: int | None = None

    @classmethod
    def read(cls, value: Any) -> Span:
        """An int, or a [low, high] pair. Never a string: nothing to parse."""
        if isinstance(value, Span):
            return value
        if isinstance(value, int):
            return cls(value)
        pair = list(value)
        return cls(pair[0]) if len(pair) == 1 else cls(pair[0], pair[1])

    @property
    def target(self) -> int:
        """The number to aim at when only one is possible.

        THE TOP OF THE RANGE WINS, everywhere this is used: stopping at the minimum
        would cap the athlete at the least he was asked for, while stopping early is
        one tap away.
        """
        return self.high if self.high is not None else self.low

    def minus(self, count: int) -> Span:
        """The whole range shifted down, floored at 1. [2,3] - 1 -> [1,2]."""
        low = max(1, self.low - count)
        high = max(low, self.high - count) if self.high is not None else None
        return Span(low, high if high != low else None)

    def human(self) -> str:
        return f"{self.low}-{self.high}" if self.high is not None else str(self.low)


def _duration(span: Span) -> str:
    """"3 min", "45 s", "1 min 30", "30-45 s"."""
    if span.high is not None:
        # A range reads in ONE unit, otherwise "1 min 30-2 min".
        if span.low % 60 == 0 and span.high % 60 == 0:
            return f"{span.low // 60}-{span.high // 60} min"
        return f"{span.low}-{span.high} s"
    if span.low < 60:
        return f"{span.low} s"
    minutes, rest = divmod(span.low, 60)
    return f"{minutes} min" if rest == 0 else f"{minutes} min {rest}"


ONE = Span(1)


@dataclass(frozen=True)
class Dose:
    measure: str                      # "reps" | "time" | "amrap" | "contacts"
    sets: Span = ONE                  # a range of sets is a real prescription
    reps: Span | None = None       # measure = reps
    seconds: Span | None = None    # measure = time, PER SET
    contacts: int | None = None    # measure = contacts
    block: int | None = None       # contacts per block
    side: str = "both"                # "each" (per leg) | "both"
    rest_s: int | None = None      # between sets, only if the session says so

    @classmethod
    def read(cls, data: dict, where: str = "") -> Dose:
        """From the frontmatter, once validated by DOSE_SCHEMA."""
        clean = DOSE_SCHEMA.validate(data, where)

        def span(name):
            return Span.read(clean[name]) if clean.get(name) is not None else None

        return cls(
            measure=clean["measure"],
            sets=span("sets") or ONE,
            reps=span("reps"),
            seconds=span("seconds"),
            contacts=clean.get("contacts"),
            block=clean.get("block"),
            side=clean.get("side") or "both",
            rest_s=clean.get("rest_s"),
        )

    def but(self, **changes) -> Dose:
        """A modulation yields a NEW dose: nothing is mutated, and the programme
        stays what it is for the next session."""
        return replace(self, **changes)

    def fewer_sets(self, count: int = 1) -> Dose:
        """The « demi-dose » modulation of strength.md: "une série en moins
        partout". A no-op on contacts, which are counted in blocks and have no
        sets to remove -- that session drops the exercise instead."""
        if self.measure == "contacts":
            return self
        return self.but(sets=self.sets.minus(count))

    @property
    def per_side(self) -> bool:
        return self.side == "each"

    @property
    def slots(self) -> list:
        """The validations the page will ask for, IN ORDER -- one dict each.

        THIS IS THE ONE PLACE THE DECOMPOSITION EXISTS. The page never rebuilds the
        list from `sets` and `side`: two implementations of one fact would diverge,
        and the plyometric BLOCKS would be lost if the page walked all contacts as a
        single slot.

        Keys are short because they ride in a URL: `n`/`of` = rank and total
        (SETS, or BLOCKS -- never the slot count, which doubles per side), `s` =
        side, `t` = what this slot aims at (seconds, reps, contacts in the block).
        """
        if self.measure == "contacts":
            contacts = _required(self.contacts, self.measure, "contacts")
            # No block declared: the whole thing is one slot.
            block = self.block or contacts
            count = contacts // block
            return [{"n": i + 1, "of": count, "t": block} for i in range(count)]

        sides = ("d", "g") if self.per_side else (None,)
        total = self.sets.target
        target = None
        if self.measure == "time":
            target = _required(self.seconds, self.measure, "seconds").target
        elif self.measure == "reps":
            target = _required(self.reps, self.measure, "reps").target

        out = []
        for rank in range(1, total + 1):
            for side in sides:
                slot: dict = {"n": rank, "of": total}
                if side:
                    slot["s"] = side
                if target is not None:
                    slot["t"] = target
                out.append(slot)
        return out

    @property
    def single_set(self) -> bool:
        return self.sets.high is None and self.sets.low == 1

    def human(self) -> str:
        """The dose as the athlete reads it. Nobody ever reads it back."""
        side = PER_SIDE if self.per_side else ""
        if self.measure == "contacts":
            block = f", en blocs de {self.block}" if self.block else ""
            return f"{self.contacts} contacts{block}"
        sets = self.sets.human()
        if self.measure == "time":
            duration = _duration(_required(self.seconds, self.measure, "seconds"))
            # "3 min", not "1 × 3 min": a single set needs no multiplier.
            return f"{duration}{side}" if self.single_set else f"{sets} × {duration}{side}"
        if self.measure == "amrap":
            return f"{sets} × {FATIGUE}{side}"
        return f"{sets} × {_required(self.reps, self.measure, 'reps').human()}{side}"

    def payload(self) -> dict:
        """What the page receives: the walked-out slots plus the already-rendered
        label. The page has nothing left to guess AND nothing left to format.

        `sets`, `side`, `seconds` and `contacts` used to travel too. They are gone:
        every one of them existed only so the page could rebuild `slots`, which it
        is now handed. What is left is what is actually displayed (`text`, and the
        per-slot `t`), what paces the session (`rest_s`), and `measure` -- which
        selects the control, not the structure.
        """
        data: dict = {"measure": self.measure, "text": self.human(), "slots": self.slots}
        if self.measure == "reps":
            data["current_text"] = f"{_required(self.reps, self.measure, 'reps').human()} répétitions"
        elif self.measure == "time":
            data["current_text"] = _duration(_required(self.seconds, self.measure, "seconds"))
        elif self.measure == "contacts":
            data["current_text"] = f"{self.block or self.contacts} contacts"
        else:
            data["current_text"] = "Jusqu’à échec technique"
        if self.rest_s:
            data["rest_s"] = self.rest_s
        return data


# --------------------------------------------------------------------- schema

def _spans(data: dict) -> list:
    """"an int, or a [low, high] pair" cannot be said with a single type: this
    is the one field shape in the repo that accepts two forms."""
    problems = []
    for name, maximum in BOUNDS.items():
        value = data.get(name)
        if value is None:
            continue
        expected = f"champ « {name} » : un entier ou une paire [bas, haut] attendu"
        if isinstance(value, bool) or not isinstance(value, (int, list)):
            problems.append(expected)
            continue
        pair = [value] if isinstance(value, int) else list(value)
        if len(pair) not in (1, 2) or any(
                isinstance(item, bool) or not isinstance(item, int) for item in pair):
            problems.append(expected)
        elif any(item < 1 or item > maximum for item in pair):
            problems.append(f"champ « {name} » : hors bornes (1 à {maximum})")
        elif len(pair) == 2 and pair[0] >= pair[1]:
            problems.append(f"champ « {name} » : fourchette {pair[0]}-{pair[1]} "
                            f"— le bas doit être inférieur au haut")
    return problems


def _consistency(data: dict) -> list:
    """What no single field can say: which measure requires what."""
    # `check` runs only once every field validated, so `measure` is present and is
    # one of MEASURES -- no need to re-ask whether it is there.
    measure = str(data["measure"])
    required = REQUIRED_BY_MEASURE.get(measure)
    problems = []
    if required and data.get(required) is None:
        problems.append(f"mesure « {measure} » : le champ « {required} » est obligatoire")
    for name in ("reps", "seconds", "contacts"):
        if name != required and data.get(name) is not None:
            problems.append(f"mesure « {measure} » : le champ « {name} » n'a pas de sens ici")
    if data.get("block") is not None:
        if measure != "contacts":
            problems.append("« block » ne s'emploie qu'avec la mesure « contacts »")
        elif data["contacts"] % data["block"]:
            problems.append(f"« block » {data['block']} ne divise pas "
                            f"{data['contacts']} contacts")
    if measure == "contacts" and data.get("sets") not in (None, 1):
        problems.append("mesure « contacts » : les contacts se comptent en blocs, pas en séries")
    return problems


def _check(data: dict) -> list:
    """Span shapes first: without them _consistency would read values it cannot
    interpret and pile up errors derived from an error."""
    return _spans(data) or _consistency(data)


DOSE_SCHEMA = Schema(
    "dose",
    [
        field("measure", str, choices=MEASURES),
        # object: a scalar or a [low, high] pair, all three checked by _spans.
        field("sets", object, required=False, note="nombre de séries"),
        field("reps", object, required=False, note="répétitions par série"),
        field("seconds", object, required=False, note="durée d'UNE série"),
        field("contacts", int, required=False, minimum=1, maximum=500),
        field("block", int, required=False, minimum=1, maximum=500),
        field("side", str, required=False, choices=SIDES),
        field("rest_s", int, required=False, minimum=5, maximum=600),
    ],
    check=_check,
)
