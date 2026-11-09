"""ISO weeks, written `2030-W39`: the one owner of their label, days and format."""

from __future__ import annotations

import datetime as dt
import re

#: A week label as every schema, route and command accepts it.
PATTERN = r"20\d{2}-W(?:0[1-9]|[1-4]\d|5[0-3])"
_LABEL = re.compile(PATTERN)


def of(day: dt.date) -> str:
    """The label of the week holding `day`."""
    year, number, _ = day.isocalendar()
    return f"{year}-W{number:02d}"


def is_label(text: str) -> bool:
    return _LABEL.fullmatch(text) is not None


def _parts(week: str) -> tuple[int, int]:
    year, _, number = week.partition("-W")
    return int(year), int(number)


def monday(week: str) -> dt.date:
    return dt.date.fromisocalendar(*_parts(week), 1)


def sunday(week: str) -> dt.date:
    return dt.date.fromisocalendar(*_parts(week), 7)


def dates(week: str) -> tuple[dt.date, ...]:
    """The seven days of the week, Monday first."""
    first = monday(week)
    return tuple(first + dt.timedelta(days=offset) for offset in range(7))


def shift(week: str, weeks: int) -> str:
    return of(monday(week) + dt.timedelta(weeks=weeks))


def short(week: str) -> str:
    """`2030-W39` -> `W39`."""
    return week.partition("-")[2]


def as_of(week: str, today: dt.date) -> dt.date:
    """The last day of `week` already lived on `today`."""
    return min(today, sunday(week))
