"""Horloge civile du coach ; le stockage conserve ses timestamps en UTC."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def now() -> datetime:
    return datetime.now(PARIS)


def today() -> date:
    return now().date()


def combine(day: date, local_time: time) -> datetime:
    return datetime.combine(day, local_time, tzinfo=PARIS)


def fromtimestamp(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=PARIS)
