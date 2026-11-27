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


def clock(value: str) -> float:
    """A duration written `H:MM:SS` or `MM:SS`, in seconds."""
    parts = value.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        numbers = []
    if len(numbers) not in (2, 3) or any(number < 0 for number in numbers) \
            or any(number >= 60 for number in numbers[1:]):
        raise argparse.ArgumentTypeError(f"duration expected as H:MM:SS or MM:SS, got {value!r}")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"a duration must be positive, got {value!r}")
    return seconds
