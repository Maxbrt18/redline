"""The instance's civil time zone, read from `redline.toml`; storage keeps UTC."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from . import instance, paths


@cache
def _zone_of(vault: Path) -> ZoneInfo:
    return instance.load(vault).timezone


def zone() -> ZoneInfo:
    """Where the athlete lives: their days, their plan's start times, their mornings."""
    return _zone_of(paths.vault_root())
