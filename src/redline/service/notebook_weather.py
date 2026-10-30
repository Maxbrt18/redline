"""The weather of an upcoming session, for the notebook's departure sheet.

Open-Meteo, free and keyless, like `redline weather` (which serves the ledger: the
weather of a COMPLETED session). Here, the forecast of an UPCOMING session: three hours
around the start, and the night hour. Nothing is stored: a forecast changes, so it is
requested again, at most every 30 minutes per place and day.

The place: the session's `location_label` if present, otherwise the notebook's default
place (`[notebook] place` in `redline.toml`). An unknown name or a missing network
breaks nothing: the sheet is shown without weather.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from redline.model import local_time

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 4
CACHE_S = 30 * 60
#: Without a planned time, the day in three moments: enough to pick a slot.
DEFAULT_HOURS = (8, 12, 18)

_lock = threading.Lock()
_places: dict[str, tuple[str, float, float] | None] = {}
_forecasts: dict[tuple[float, float, str], tuple[float, dict]] = {}


def _get(url: str, params: dict) -> Any:
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{query}", timeout=TIMEOUT_S) as response:
        return json.load(response)


def place(name: str) -> tuple[str, float, float] | None:
    """(name, latitude, longitude) of a French place, or None if not found."""
    key = name.strip().lower()
    with _lock:
        if key in _places:
            return _places[key]
    data = _get(GEOCODE_URL, {"name": name, "count": 1, "language": "fr",
                              "countryCode": "FR"})
    found = (data.get("results") or [None])[0]
    value = (found["name"], float(found["latitude"]), float(found["longitude"])) if found else None
    with _lock:
        _places[key] = value
    return value


def _forecast(lat: float, lon: float, day: dt.date) -> dict:
    key = (round(lat, 3), round(lon, 3), day.isoformat())
    with _lock:
        cached = _forecasts.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_S:
            return cached[1]
    data = _get(FORECAST_URL, {
        "latitude": lat, "longitude": lon, "timezone": local_time.zone().key,
        "start_date": day.isoformat(), "end_date": day.isoformat(),
        "hourly": "temperature_2m,apparent_temperature,precipitation_probability,"
                  "wind_speed_10m,weather_code,is_day",
        "daily": "sunrise,sunset"})
    with _lock:
        _forecasts[key] = (time.monotonic(), data)
    return data


def hours_for(day: dt.date, start: str | None, now: dt.datetime) -> list[int]:
    """The hours to show: around the planned start, otherwise the moments that remain."""
    if start:
        first = int(start[:2])
        return [hour for hour in (first, first + 1, first + 2) if hour <= 23]
    if day == now.date():
        return list(range(now.hour + 1, 23))[:3]
    return list(DEFAULT_HOURS)


def forecast(name: str, day: dt.date, start: str | None, now: dt.datetime) -> dict | None:
    """The readable forecast of a session, or None if nothing is available."""
    hours = hours_for(day, start, now)
    if not hours or not 0 <= (day - now.date()).days <= 6:
        return None
    try:
        found = place(name)
        if found is None:
            return None
        label, lat, lon = found
        data = _forecast(lat, lon, day)
    except (OSError, ValueError, KeyError):
        return None
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    out = []
    for hour in hours:
        stamp = f"{day.isoformat()}T{hour:02d}:00"
        if stamp not in times:
            continue
        index = times.index(stamp)
        out.append({
            "hour": hour,
            "temp": round(hourly["temperature_2m"][index]),
            "feels": round(hourly["apparent_temperature"][index]),
            "rain": hourly["precipitation_probability"][index],
            "wind": round(hourly["wind_speed_10m"][index]),
            # The sky as a WMO code and the day flag: the sheet's icon and word.
            "code": (hourly.get("weather_code") or [None] * len(times))[index],
            "day": bool((hourly.get("is_day") or [1] * len(times))[index]),
        })
    daily = data.get("daily") or {}
    sunrise, sunset = (daily.get("sunrise") or [None])[0], (daily.get("sunset") or [None])[0]
    return {"place": label, "date": day.isoformat(), "hours": out,
            "sunrise": sunrise[11:16] if sunrise else None,
            "sunset": sunset[11:16] if sunset else None} if out else None
