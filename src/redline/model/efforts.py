"""The efforts an athlete declared run all out, and the training breaks declared since.

Both are declared with the athlete, never inferred: neither a session's kind nor its RPE
proves an effort was maximal, and a break is a stretch the athlete names.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .vocabulary import EffortKind, EffortTerrain


@dataclass(frozen=True)
class Effort:
    key: str
    on: dt.date
    km: float
    seconds: float
    terrain: EffortTerrain
    kind: EffortKind
    note: str = ""

    @property
    def minutes(self) -> float:
        return self.seconds / 60

    @property
    def flat(self) -> bool:
        return self.terrain is EffortTerrain.FLAT


@dataclass(frozen=True)
class Break:
    key: str
    starts_on: dt.date
    ends_on: dt.date
    note: str = ""

    @property
    def days(self) -> int:
        return (self.ends_on - self.starts_on).days + 1


def _day(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def efforts(rows: Iterable[Mapping[str, Any]]) -> list[Effort]:
    """The stored efforts (`all_out_efforts`), oldest first."""
    found = [Effort(str(row["effort_key"]), _day(row["done_on"]), float(row["distance_km"]),
                    float(row["duration_s"]), EffortTerrain(row["terrain"]),
                    EffortKind(row["kind"]), str(row.get("note") or ""))
             for row in rows]
    return sorted(found, key=lambda effort: (effort.on, effort.key))


def breaks(rows: Iterable[Mapping[str, Any]]) -> list[Break]:
    """The stored breaks (`training_breaks`), oldest first."""
    found = [Break(str(row["break_key"]), _day(row["starts_on"]), _day(row["ends_on"]),
                   str(row.get("note") or ""))
             for row in rows]
    return sorted(found, key=lambda pause: (pause.starts_on, pause.key))
