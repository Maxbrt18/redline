"""A race plan, assembled once for every reader: the command today, a notebook page later.

Takes a parsed route and what the instance declares, returns a typed report: profile
totals, passages with their band, clock time and forecast, and fuelling per stretch. The
outside world comes in as callables (terrain elevation, forecast, time zone of a place), so
the assembly never reaches the network by itself and a reader can supply cached versions.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from zoneinfo import ZoneInfo

from redline.model.errors import InputError
from redline.model.vocabulary import ElevationSource

from . import race_pace, route_profile

#: (lat, lon, aware datetime) -> the hour's weather, or raises RuntimeError/OSError when the
#: date has no forecast yet.
Forecaster = Callable[[float, float, dt.datetime], dict]
#: (lat, lon, naive local time) -> the same time, aware, in the place's zone.
ZoneResolver = Callable[[float, float, dt.datetime], dt.datetime]

#: The grade curve when the instance declares none: Strava 2017 both ways.
STRAVA_ONLY = race_pace.GradeCurve()

#: Fuelling ceiling when the rulebook declares none (`planned_sessions` CHECK).
CARBS_LIMIT_G_H = 150


@dataclass(frozen=True)
class Forecast:
    temp_c: float
    feels_like_c: float
    wind_kmh: float
    precip_mm_h: float


@dataclass(frozen=True)
class PassageReport:
    km: float
    #: Empty for the finish.
    name: str
    #: Hours from the start: central, median band, fast bound of the curve.
    hours: float
    low_hours: float
    high_hours: float
    fast_hours: float
    running_hours: float
    clock: dt.datetime | None
    forecast: Forecast | None
    carbs_g: float | None


@dataclass(frozen=True)
class RaceReport:
    name: str
    source: ElevationSource
    length_km: float
    flat_equivalent_km: float
    dplus_m: float
    dminus_m: float
    dplus_band_m: tuple[float, float] | None
    pace_s_per_km: float
    stop_minutes: float
    curve_declared: bool
    has_fast_bound: bool
    carbs_g_h: int | None
    carbs_mixed: bool
    #: None without a start time; False when the date has no forecast yet.
    forecast_available: bool | None
    model_error: tuple[float, float]
    #: The finish band nine times out of ten, in hours.
    finish_p90_hours: tuple[float, float]
    passages: tuple[PassageReport, ...]

    @property
    def finish(self) -> PassageReport:
        return self.passages[-1]

    def as_dict(self) -> dict:
        """Plain data: datetimes as ISO strings, enums as their value."""
        payload = asdict(self)
        payload["source"] = self.source.value
        for passage in payload["passages"]:
            if passage["clock"] is not None:
                passage["clock"] = passage["clock"].isoformat()
        return payload


def _forecast(raw: dict) -> Forecast | None:
    values = [raw.get(key) for key in ("temp", "feels_like", "wind", "precip")]
    numbers = [float(value) for value in values if value is not None]
    return Forecast(*numbers) if len(numbers) == len(values) else None


def build(track: route_profile.Track, *, sampler: route_profile.ElevationSampler | None = None,
          **plan) -> RaceReport:
    """The report of a route read from its GPX; `plan` as for `assemble`."""
    return assemble(route_profile.profile(track, sampler), **plan)


def assemble(profile: route_profile.Profile, *, pace_s_per_km: float,
             aids: Sequence[race_pace.AidStation] = (), stop_minutes: float = 0.0,
             curve: race_pace.GradeCurve = STRAVA_ONLY,
             carbs_g_h: int | None = None, carbs_cap_g_h: int | None = None,
             carbs_mix_above_g_h: int | None = None,
             start_local: dt.datetime | None = None, zone: ZoneInfo | None = None,
             resolve_zone: ZoneResolver | None = None,
             forecaster: Forecaster | None = None) -> RaceReport:
    """The report of a profile already computed: a reader that keeps profiles in a cache
    plans without measuring the route again."""
    if not 0 <= stop_minutes <= 30:
        raise InputError("stops: minutes per aid station, 0 to 30")
    if carbs_g_h is not None and not 0 < carbs_g_h <= (carbs_cap_g_h or CARBS_LIMIT_G_H):
        raise InputError(f"carbohydrate {carbs_g_h} g/h: from 1 to "
                         f"{carbs_cap_g_h or CARBS_LIMIT_G_H} g/h (rule f1)")
    if start_local is not None and start_local.tzinfo is not None:
        raise InputError("the start is a local wall-clock time, without an offset")
    plan = race_pace.plan(profile.distance_m, profile.elevation_m, curve, pace_s_per_km,
                          aids, stop_minutes)
    start = None
    if start_local is not None:
        if zone is not None:
            start = start_local.replace(tzinfo=zone)
        elif resolve_zone is not None:
            start = resolve_zone(*profile.position(0.0), start_local)
        else:
            raise InputError("a start time needs the time zone of the start")
    forecasts: list[Forecast | None] = [None] * len(plan.passages)
    available = None
    if start is not None and forecaster is not None:
        try:
            forecasts = [_forecast(forecaster(*profile.position(p.km),
                                              start + dt.timedelta(hours=p.hours)))
                         for p in plan.passages]
            available = True
        except (RuntimeError, OSError):
            available = False
    median, p90 = race_pace.MODEL_ERROR
    passages = tuple(PassageReport(
        km=p.km, name=p.name, hours=p.hours, low_hours=p.hours * (1 - median),
        high_hours=p.hours * (1 + median), fast_hours=p.fast_hours,
        running_hours=p.running_hours,
        clock=start + dt.timedelta(hours=p.hours) if start is not None else None,
        forecast=forecast, carbs_g=p.running_hours * carbs_g_h if carbs_g_h else None)
        for p, forecast in zip(plan.passages, forecasts, strict=True))
    return RaceReport(
        name=profile.name, source=profile.source, length_km=profile.length_km,
        flat_equivalent_km=plan.flat_equivalent_km, dplus_m=profile.dplus_m,
        dminus_m=profile.dminus_m, dplus_band_m=profile.dplus_band_m,
        pace_s_per_km=pace_s_per_km, stop_minutes=stop_minutes,
        curve_declared=bool(curve.descent), has_fast_bound=bool(curve.training_climb),
        carbs_g_h=carbs_g_h,
        carbs_mixed=bool(carbs_g_h and carbs_mix_above_g_h is not None
                         and carbs_g_h > carbs_mix_above_g_h),
        forecast_available=available, model_error=race_pace.MODEL_ERROR,
        finish_p90_hours=(plan.finish.hours * (1 - p90), plan.finish.hours * (1 + p90)),
        passages=passages)
