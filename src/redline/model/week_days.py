"""How each day of a week presents itself to the athlete: what a reorganisation works with.

The athlete names a day by what they can do, never by why. Only the typed fields steer a
reorganisation: a sport's name is shown to the athlete, never read by the engine.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .schema import Schema, SchemaError, field
from .vocabulary import DayKind, GroupPace

DATE_RE = r"20\d{2}-\d{2}-\d{2}"
SESSION_RE = r"[a-z0-9][a-z0-9-]+"

#: The fields each kind of day carries; every other optional field is refused.
DETAILS: dict[DayKind, tuple[str, ...]] = {
    DayKind.AS_PLANNED: (),
    DayKind.OFF: (),
    DayKind.SHORT: ("minutes",),
    DayKind.EASY: (),
    DayKind.MORE: (),
    DayKind.WITH_OTHERS: ("minutes", "pace", "required"),
    DayKind.OTHER_SPORT: ("sport", "demanding", "run_too"),
}
_OPTIONAL = ("minutes", "pace", "required", "sport", "demanding", "run_too")


def _coherent(day: dict) -> list[str]:
    expected = DETAILS[DayKind(day["kind"])]
    problems = [f"field `{name}`: required for a {day['kind']} day"
                for name in expected if day.get(name) is None]
    problems += [f"field `{name}`: only for {', '.join(kind for kind, names in DETAILS.items() if name in names)}"
                 for name in _OPTIONAL if name not in expected and day.get(name) is not None]
    return problems


DAY_SCHEMA = Schema("day", [
    field("date", str, pattern=DATE_RE, note="YYYY-MM-DD"),
    field("kind", str, choices=tuple(DayKind)),
    field("minutes", int, required=False, minimum=10, maximum=600,
          note="short: the most the day holds; with_others: how long the group runs"),
    field("pace", str, required=False, choices=tuple(GroupPace),
          note="with_others: how the group moves"),
    field("required", bool, required=False,
          note="with_others: the run with the group must happen"),
    field("sport", str, required=False, minimum=1, maximum=40,
          note="other_sport: its name, shown only"),
    field("demanding", bool, required=False,
          note="other_sport: it counts as a hard day"),
    field("run_too", bool, required=False,
          note="other_sport: a run may still go on that day"),
], check=_coherent)

PIN_SCHEMA = Schema("pinned session", [
    field("session", str, pattern=SESSION_RE, note="the id of a session of the plan"),
    field("date", str, pattern=DATE_RE, note="YYYY-MM-DD"),
])

REQUEST_SCHEMA = Schema("reorganisation request", [
    field("days", list, maximum=7, item=field("day", dict, schema=DAY_SCHEMA)),
    field("pins", list, required=False, maximum=7, item=field("pin", dict, schema=PIN_SCHEMA)),
])


@dataclass(frozen=True)
class Day:
    date: dt.date
    kind: DayKind
    minutes: int | None = None
    pace: GroupPace | None = None
    required: bool = False
    sport: str | None = None
    demanding: bool = False
    run_too: bool = False

    @property
    def closed(self) -> bool:
        """No session at all goes on this day."""
        return self.kind is DayKind.OFF or (self.kind is DayKind.OTHER_SPORT and not self.run_too)

    @property
    def hard(self) -> bool:
        """The day is already a hard one, whatever the plan puts on it."""
        return self.kind is DayKind.OTHER_SPORT and self.demanding


@dataclass(frozen=True)
class Request:
    days: dict[dt.date, Day]
    pins: dict[str, dt.date]

    def day(self, date: dt.date) -> Day:
        return self.days.get(date) or Day(date, DayKind.AS_PLANNED)


def week_dates(week: str) -> tuple[dt.date, ...]:
    monday = dt.date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)
    return tuple(monday + dt.timedelta(days=offset) for offset in range(7))


def parse(payload: object, week: str) -> Request:
    """A request read once at the boundary: dates inside `week`, each day stated once."""
    clean = REQUEST_SCHEMA.validate(payload, "request")
    dates = set(week_dates(week))
    problems, days, pins = [], {}, {}
    for entry in clean["days"]:
        date = dt.date.fromisoformat(entry["date"])
        if date not in dates:
            problems.append(f"day {entry['date']}: outside {week}")
        elif date in days:
            problems.append(f"day {entry['date']}: stated twice")
        else:
            days[date] = Day(
                date, DayKind(entry["kind"]), minutes=entry.get("minutes"),
                pace=GroupPace(entry["pace"]) if entry.get("pace") else None,
                required=bool(entry.get("required")), sport=entry.get("sport"),
                demanding=bool(entry.get("demanding")), run_too=bool(entry.get("run_too")))
    for entry in clean.get("pins") or []:
        date = dt.date.fromisoformat(entry["date"])
        if date not in dates:
            problems.append(f"pin {entry['session']}: {entry['date']} outside {week}")
        elif entry["session"] in pins:
            problems.append(f"pin {entry['session']}: pinned twice")
        else:
            pins[entry["session"]] = date
    if problems:
        raise SchemaError(problems)
    return Request(days, pins)
