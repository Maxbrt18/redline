"""A recorded activity as the engine keeps it: parsed once, stored as is, derived on read.

`strava.parse_activity` builds it from the provider's payload; the storage keeps
`to_json()` in `strava_activity_intakes.distilled` and every reader rebuilds it with
`from_json`. Pace and speed are computed from distance and time, never stored.
`summary()` is the readable form the coach receives.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from . import session_kinds
from .errors import InputError


class ActivityError(InputError):
    """A stored activity that does not match this shape."""


@dataclass(frozen=True)
class Lap:
    name: str | None = None
    distance_m: float = 0.0
    moving_s: int = 0
    hr_avg_bpm: float | None = None
    hr_max_bpm: float | None = None
    elevation_gain_m: float | None = None
    #: Steps per minute, both feet: set for a sport on foot.
    cadence_spm: float | None = None
    #: Crank revolutions per minute: set for any other sport.
    cadence_rpm: float | None = None


@dataclass(frozen=True)
class Activity:
    id: int
    sport: str
    name: str | None = None
    description: str = ""
    #: Wall-clock time at the start, ISO 8601 without an offset.
    start_local: str | None = None
    distance_m: float = 0.0
    moving_s: int = 0
    elapsed_s: int | None = None
    elevation_gain_m: float | None = None
    hr_avg_bpm: float | None = None
    hr_max_bpm: float | None = None
    cadence_spm: float | None = None
    cadence_rpm: float | None = None
    relative_effort: float | None = None
    calories: float | None = None
    gear_id: str | None = None
    athlete_count: int | None = None
    #: Encoded summary route of an outdoor activity; None indoors.
    route_polyline: str | None = None
    laps: tuple[Lap, ...] = ()

    @property
    def on_foot(self) -> bool:
        """Pace in min/km and cadence in steps, rather than speed and revolutions."""
        return self.sport in session_kinds.FOOT_SPORTS

    @property
    def distance_km(self) -> float:
        return round(self.distance_m / 1000, 3)

    @property
    def pace_s_per_km(self) -> int | None:
        return _pace(self.distance_m, self.moving_s) if self.on_foot else None

    @property
    def weather_query(self) -> dict[str, str] | None:
        """Where and when to read the weather: the start of the route, local time."""
        if not self.route_polyline or not self.start_local:
            return None
        return {"polyline": self.route_polyline, "time": self.start_local}

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["laps"] = [asdict(lap) for lap in self.laps]
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Activity:
        try:
            laps = tuple(Lap(**lap) for lap in data.get("laps") or ())
            return cls(**{**data, "laps": laps})
        except TypeError as exc:
            raise ActivityError(f"unreadable stored activity: {exc}") from exc


def _pace(distance_m: float, moving_s: int) -> int | None:
    if not distance_m or not moving_s:
        return None
    return round(moving_s / (distance_m / 1000))


def clock(seconds: int | None) -> str | None:
    """`m:ss`, or `h:mm:ss` from an hour; rounded on the total so 59.6 s never reads 0:60."""
    if not seconds:
        return None
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


#: Stored as numbers, shown to the coach in a readable form instead.
_READABLE = {"distance_m", "moving_s", "elapsed_s", "laps"}
#: Never shown: a route is thousands of characters that mean nothing to the coach.
_HIDDEN = {"route_polyline"}


def _readable(item: Activity | Lap, on_foot: bool) -> dict[str, Any]:
    out = {field.name: getattr(item, field.name) for field in fields(item)
           if field.name not in _READABLE | _HIDDEN}
    out["distance_km"] = round(item.distance_m / 1000, 3)
    out["moving_time"] = clock(item.moving_s)
    pace = _pace(item.distance_m, item.moving_s)
    if on_foot:
        out["pace_min_per_km"] = clock(pace)
    elif pace:
        out["speed_kmh"] = round(item.distance_m / item.moving_s * 3.6, 1)
    return out


def summary(activity: Activity) -> dict[str, Any]:
    """The activity as the coach reads it, without its laps or empty values."""
    out = _readable(activity, activity.on_foot)
    out["elapsed_time"] = clock(activity.elapsed_s)
    out["lap_count"] = len(activity.laps)
    return {key: value for key, value in out.items() if value not in (None, "")}


def lap_summaries(activity: Activity) -> list[dict[str, Any]]:
    """Each lap as the coach reads it, numbered from 1."""
    return [{key: value for key, value in {"lap": index, **_readable(lap, activity.on_foot)}
             .items() if value not in (None, "")}
            for index, lap in enumerate(activity.laps, start=1)]
