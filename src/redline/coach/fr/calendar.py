"""Days and months as the coach writes them."""

from __future__ import annotations

DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
#: One letter per day, for a calendar header.
DAY_LETTERS = tuple(day[0].upper() for day in DAYS)
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre")


def listing(items: list[str]) -> str:
    """`a`, `a et b`, `a, b et c`."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " et " + items[-1]
