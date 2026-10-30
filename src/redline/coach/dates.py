"""Days, months and lists as the athlete reads them, in the instance's locale.

Babel carries the names: `strftime` would depend on the locales installed on the host.
The locale is `[athlete] locale` in `redline.toml`; without a readable instance (a
script run outside any vault), the default one.
"""

from __future__ import annotations

import datetime as dt
from functools import cache
from pathlib import Path

from babel import Locale
from babel.dates import format_date
from babel.lists import format_list

from redline.model import instance, paths
from redline.model.errors import ConfigError


@cache
def _locale_of(vault: Path) -> Locale:
    return Locale.parse(instance.load(vault).locale)


def locale() -> Locale:
    try:
        return _locale_of(paths.vault_root())
    except ConfigError:
        return Locale.parse(instance.DEFAULT_LOCALE)


def weekday(day: dt.date, width: str = "wide") -> str:
    """`lundi`; `abbreviated` gives `lun.`, `narrow` gives `L`."""
    pattern = {"wide": "EEEE", "abbreviated": "EEE", "narrow": "EEEEE"}[width]
    return format_date(day, pattern, locale=locale())


def day_month(day: dt.date) -> str:
    """`11 mars`."""
    return format_date(day, "d MMMM", locale=locale())


def full_date(day: dt.date) -> str:
    """`lundi 11 mars 2030`."""
    return format_date(day, "EEEE d MMMM y", locale=locale())


def listing(items: list[str]) -> str:
    """`a`, `a et b`, `a, b et c`."""
    return format_list(items, locale=locale())
