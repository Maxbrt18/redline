"""Whether a run was in the dark: the sun's elevation along it, computed offline.

The elevation follows NOAA's solar position equations (declination and equation of
time from the fractional year), accurate to a fraction of a degree, enough to tell day
from night. A moment is dark below civil twilight, the sun `CIVIL_TWILIGHT_DEG` under
the horizon: past it, the road needs a lamp. A run is read every `STEP_S` seconds from
its start to its end.
"""

from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

CIVIL_TWILIGHT_DEG = -6.0
STEP_S = 300


def elevation(latitude: float, longitude: float, moment: dt.datetime) -> float:
    """The sun's elevation above the horizon, in degrees, at an aware `moment`."""
    utc = moment.astimezone(dt.UTC)
    day_of_year = utc.timetuple().tm_yday
    hours = utc.hour + utc.minute / 60 + utc.second / 3600
    gamma = 2 * math.pi / 365 * (day_of_year - 1 + (hours - 12) / 24)
    equation_min = 229.18 * (0.000075 + 0.001868 * math.cos(gamma)
                             - 0.032077 * math.sin(gamma) - 0.014615 * math.cos(2 * gamma)
                             - 0.040849 * math.sin(2 * gamma))
    declination = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
                   - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
                   - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    solar_min = hours * 60 + equation_min + 4 * longitude
    hour_angle = math.radians(solar_min / 4 - 180)
    lat = math.radians(latitude)
    cos_zenith = (math.sin(lat) * math.sin(declination)
                  + math.cos(lat) * math.cos(declination) * math.cos(hour_angle))
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


def dark_share(latitude: float, longitude: float, start_local: dt.datetime, seconds: float,
               zone: ZoneInfo) -> float:
    """The share of a run spent below civil twilight; `start_local` is wall-clock time in
    `zone`."""
    start = start_local.replace(tzinfo=zone)
    steps = max(int(seconds // STEP_S), 0) + 1
    dark = sum(1 for index in range(steps)
               if elevation(latitude, longitude,
                            start + dt.timedelta(seconds=index * STEP_S)) < CIVIL_TWILIGHT_DEG)
    return dark / steps
