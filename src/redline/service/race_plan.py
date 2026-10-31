"""The race plan of the typed race, for the notebook's Race tab and its plan screen.

`analysis.race_report` computes it; this module brings it what a page cannot: the route
read from the vault, the terrain elevation kept on disk, and the forecast at each passage.
The plan is computed at one reference pace (the chosen one, or REFERENCE_PACE_S): running
time is proportional to the pace and the stops are not, so the page retimes it at any pace
without asking again.

The terrain model is asked once per route: the elevations are kept in the state folder,
named by the route's points. Only the official route is ever sent, never a recorded run.
Asking takes some twenty seconds, so it never happens inside a request: the first one
starts it in the background and answers `Pending`. Anything missing (no `route` extra, a
GPX gone from the vault, the terrain service down) costs the plan or its elevation
source, never the Race tab.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from redline.model import notebook_views as views
from redline.model import paths
from redline.model import race as race_model
from redline.model.errors import ExternalError, InputError
from redline.model.instance import Instance

from . import notebook_weather

if TYPE_CHECKING:
    from redline.analysis import route_profile

log = logging.getLogger(__name__)

#: The pace the passages are timed at when none is chosen: any pace would do, since the
#: page scales them, and none is shown.
REFERENCE_PACE_S = 360.0
#: One profile point every this many metres: enough for a phone-wide chart.
PROFILE_STEP_M = 200
#: The forecast reaches about this far: before, the plan has no weather yet. The page
#: announces the same date (`raceplan.js`).
FORECAST_DAYS = 15
#: Hours of forecast kept after the start, whatever pace the page explores.
NIGHT_HOURS = 14

_lock = threading.Lock()
_profiles: dict[tuple[str, int], route_profile.Profile] = {}
_measuring: set[tuple[str, int]] = set()


class Pending(ExternalError):
    """The route's terrain elevation is being measured by the terrain service: ask again
    in a minute."""


def _route(root: Path, race: race_model.Race) -> Path:
    path = (root / (race.route_gpx or "")).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise InputError(f"route {race.route_gpx!r}: not a file of the vault")
    return path


def _cache(lats: Sequence[float], lons: Sequence[float]) -> Path:
    key = hashlib.sha256(json.dumps([list(lats), list(lons)]).encode()).hexdigest()[:32]
    return paths.state_dir() / "elevation" / f"{key}.json"


def _stored_elevations(lats: Sequence[float], lons: Sequence[float]) -> list[float | None]:
    """The terrain elevations kept on disk, or Pending when they were never measured."""
    try:
        return json.loads(_cache(lats, lons).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Pending from None


def _measured_elevations(lats: Sequence[float], lons: Sequence[float]) -> list[float | None]:
    """The terrain elevations, asked and then kept. A failed request falls back to the GPX
    elevation and is asked again at the next start."""
    from redline.integrations import ign_altimetry

    cache = _cache(lats, lons)
    try:
        values = ign_altimetry.elevations(lats, lons)
    except Exception as exc:
        log.warning("Race plan: terrain elevation unavailable (%s)", type(exc).__name__)
        return [None] * len(lats)
    if all(value is not None for value in values):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(values), encoding="utf-8")
    return values


def _keep(key: tuple[str, int], found: route_profile.Profile) -> None:
    with _lock:
        _profiles.clear()
        _profiles[key] = found
        _measuring.discard(key)


def _measure(key: tuple[str, int], path: Path) -> None:
    from redline.analysis import route_profile

    try:
        _keep(key, route_profile.profile(route_profile.read_gpx(path), _measured_elevations))
    except Exception as exc:
        log.warning("Race plan: route unreadable (%s)", type(exc).__name__)
        with _lock:
            _measuring.discard(key)


def _profile(path: Path) -> route_profile.Profile:
    from redline.analysis import route_profile

    key = (str(path), path.stat().st_mtime_ns)
    with _lock:
        if key in _profiles:
            return _profiles[key]
        if key in _measuring:
            raise Pending
    try:
        found = route_profile.profile(route_profile.read_gpx(path), _stored_elevations)
    except Pending:
        with _lock:
            _measuring.add(key)
        threading.Thread(target=_measure, args=(key, path), daemon=True,
                         name="race-plan-elevation").start()
        raise
    _keep(key, found)
    return found


def _start(race: race_model.Race) -> dt.datetime | None:
    return dt.datetime.combine(race.on, race.start_time) if race.start_time else None


def _plan(race: race_model.Race, root: Path, instance: Instance | None
          ) -> tuple[views.RacePlan, route_profile.Profile]:
    from redline.analysis import race_pace, race_report
    from redline.model import rulebook

    profile = _profile(_route(root, race))
    parameters = rulebook.current().parameters
    curve = (race_pace.GradeCurve(instance.descent_speed, instance.training_climb_speed)
             if instance else race_report.STRAVA_ONLY)
    report = race_report.assemble(
        profile, pace_s_per_km=race.pace_s_per_km or REFERENCE_PACE_S,
        aids=[race_pace.AidStation(aid.km, aid.name) for aid in race.aid_stations],
        stop_minutes=race.stop_minutes, curve=curve, carbs_g_h=race.carbs_g_h,
        carbs_cap_g_h=parameters.carbs_cap_g_h,
        carbs_mix_above_g_h=parameters.carbs_mix_above_g_h)
    step = max(1, round(PROFILE_STEP_M / float(profile.distance_m[1] - profile.distance_m[0])))
    points = [[round(float(km) / 1000, 2), round(float(m))]
              for km, m in zip(profile.distance_m[::step], profile.elevation_m[::step],
                               strict=True)]
    if points[-1][0] < round(profile.length_km, 2):
        points.append([round(profile.length_km, 2), round(float(profile.elevation_m[-1]))])
    cuts = [0.0, *(passage.km for passage in report.passages)]
    stretches: list[views.RouteStretch] = []
    for begin, end in itertools.pairwise(cuts):
        dplus, dminus = profile.climb(begin, end)
        stretches.append({"to_km": round(end, 2), "dplus_m": round(dplus),
                          "dminus_m": round(dminus)})
    high_m, high_km = profile.highest()
    start = _start(race)
    plan: views.RacePlan = {
        "source": "ign" if report.source.value == "ign" else "gpx",
        "length_km": round(report.length_km, 2), "dplus_m": round(report.dplus_m),
        "dminus_m": round(report.dminus_m),
        "dplus_band_m": [round(value) for value in report.dplus_band_m]
        if report.dplus_band_m else None,
        "profile": points, "highest": [round(high_m), round(high_km, 2)],
        "stretches": stretches,
        "passages": [{"km": round(passage.km, 2), "name": passage.name,
                      "hours": passage.hours, "running_hours": passage.running_hours}
                     for passage in report.passages],
        "reference_pace_s": report.pace_s_per_km, "pace_s_per_km": race.pace_s_per_km,
        "stop_minutes": race.stop_minutes, "carbs_g_h": race.carbs_g_h,
        "model_error": list(report.model_error),
        "start": start.isoformat(timespec="minutes") if start else None}
    return plan, profile


def plan(race: race_model.Race | None, root: Path,
         instance: Instance | None) -> views.RacePlan | None:
    """The plan for the Race tab, or None when the race declares no route or it cannot be
    read."""
    if race is None or not race.route_gpx:
        return None
    try:
        return _plan(race, root, instance)[0]
    except (ImportError, Pending):
        return None
    except Exception as exc:
        log.warning("Race plan unavailable: %s", type(exc).__name__)
        return None


def _weather(profile: route_profile.Profile, plan_view: views.RacePlan, today: dt.date
             ) -> list[list[views.PassageHour]] | None:
    if plan_view["start"] is None:
        return None
    start = dt.datetime.fromisoformat(plan_view["start"])
    end = start + dt.timedelta(hours=NIGHT_HOURS)
    # The page announces the forecast FORECAST_DAYS before the start: the same day here.
    if (start.date() - today).days > FORECAST_DAYS or end.date() < today:
        return None
    days = [start.date() + dt.timedelta(days=offset)
            for offset in range((end.date() - start.date()).days + 1)]
    hours = [start.replace(minute=0) + dt.timedelta(hours=offset)
             for offset in range(NIGHT_HOURS + 1)]
    try:
        return [notebook_weather.hourly(*profile.position(passage["km"]), days, hours)
                for passage in plan_view["passages"]]
    except (OSError, ValueError, KeyError) as exc:
        log.warning("Race plan: forecast unavailable (%s)", type(exc).__name__)
        return None


def view(race: race_model.Race | None, root: Path, instance: Instance | None,
         today: dt.date) -> views.RacePlanView | None:
    """The plan screen: the plan, and the forecast at each passage once it exists. Raises
    Pending while the route is being measured."""
    if race is None or not race.route_gpx:
        return None
    try:
        plan_view, profile = _plan(race, root, instance)
    except Pending:
        raise
    except ImportError:
        return None
    except Exception as exc:
        log.warning("Race plan unavailable: %s", type(exc).__name__)
        return None
    return {"plan": plan_view, "weather": _weather(profile, plan_view, today)}

