"""Argument types every command shares (not a command: its name starts with `_`)."""

from __future__ import annotations

import argparse
import datetime as dt

from redline.model import iso_week


def week(value: str) -> str:
    if not iso_week.is_label(value):
        raise argparse.ArgumentTypeError(f"ISO week expected as YYYY-Www (2030-W33), got {value!r}")
    return value


def day(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"date expected as YYYY-MM-DD, got {value!r}") from None
