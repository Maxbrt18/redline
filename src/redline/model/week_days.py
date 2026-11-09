"""How each day of a week presents itself to the athlete: what a reorganisation works with.

The athlete names a day by what they can do, never by why. Only the typed fields steer a
reorganisation: a sport's name is shown to the athlete, never read by the engine.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import formats, iso_week
from .schema import Schema, SchemaError, field
from .vocabulary import DayKind, GroupPace

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


STATE_FIELDS = [
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
]

DAY_SCHEMA = Schema("day", [field("date", str, pattern=formats.DATE, note="YYYY-MM-DD"),
                            *STATE_FIELDS], check=_coherent)


def _a_habit_changes_something(habit: dict) -> list[str]:
    if habit.get("kind") == DayKind.AS_PLANNED:
        return ["field `kind`: a habit that changes nothing is not one"]
    return _coherent(habit)


HABIT_SCHEMA = Schema("habit", [field("weekday", int, minimum=1, maximum=7,
                                      note="ISO weekday, 1 = Monday"),
                                *STATE_FIELDS], check=_a_habit_changes_something)

PIN_SCHEMA = Schema("pinned session", [
    field("session", str, pattern=formats.KEY, note="the id of a session of the plan"),
    field("date", str, pattern=formats.DATE, note="YYYY-MM-DD"),
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
    #: The day comes from a weekly habit rather than from what was stated for it.
    habit: bool = False

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


def parse(payload: object, week: str) -> Request:
    """A request read once at the boundary: dates inside `week`, each day stated once."""
    clean = REQUEST_SCHEMA.validate(payload, "request")
    dates = set(iso_week.dates(week))
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


def state_row(day: Day) -> dict:
    """The columns a day state is stored in."""
    return {"kind": day.kind.value, "minutes": day.minutes,
            "pace": day.pace.value if day.pace else None,
            "required": day.required if day.kind is DayKind.WITH_OTHERS else None,
            "sport": day.sport,
            "demanding": day.demanding if day.kind is DayKind.OTHER_SPORT else None,
            "run_too": day.run_too if day.kind is DayKind.OTHER_SPORT else None}


def from_row(date: dt.date, row: dict, *, habit: bool = False) -> Day:
    return Day(date, DayKind(row["kind"]), minutes=row.get("minutes"),
               pace=GroupPace(row["pace"]) if row.get("pace") else None,
               required=bool(row.get("required")), sport=row.get("sport"),
               demanding=bool(row.get("demanding")), run_too=bool(row.get("run_too")),
               habit=habit)


def habit_on(habits: list[dict], date: dt.date) -> dict | None:
    """The habit in force on `date`, the latest started when two overlap."""
    found = [row for row in habits if row["weekday"] == date.isoweekday()
             and row["starts_on"] <= date and (row["ends_on"] is None or row["ends_on"] >= date)]
    return max(found, key=lambda row: (row["starts_on"], str(row["id"])), default=None)


def effective(week: str, habits: list[dict], stated: list[dict], request: Request) -> Request:
    """Each day as it stands: what was just touched, else what was stated for it, else its
    habit. A day stated as planned clears its habit for that week."""
    days: dict[dt.date, Day] = {}
    for date in iso_week.dates(week):
        habit = habit_on(habits, date)
        if habit is not None:
            days[date] = from_row(date, habit, habit=True)
    for row in stated:
        days[row["day"]] = from_row(row["day"], row)
    days.update(request.days)
    return Request({date: day for date, day in days.items() if day.kind is not DayKind.AS_PLANNED},
                   request.pins)
