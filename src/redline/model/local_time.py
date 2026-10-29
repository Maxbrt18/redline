"""The instance's civil time zone, read from `redline.toml`; storage keeps UTC."""

from __future__ import annotations

import os
import time
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from . import instance, paths
from .errors import ConfigError


@cache
def _zone_of(vault: Path) -> ZoneInfo:
    return instance.load(vault).timezone


def zone() -> ZoneInfo:
    """Where the athlete lives: their days, their plan's start times, their mornings."""
    return _zone_of(paths.vault_root())


def apply_to_process() -> None:
    """Set the process clock to the athlete's zone, for the code that reads naive local
    time and for every subprocess it starts. Without a vault or a readable instance
    (a card rendered from a sample, for instance), nothing."""
    try:
        name = zone().key
    except ConfigError:
        return
    os.environ["TZ"] = name
    time.tzset()
