"""The `redline race-plan` command: passage times on a race route at a held pace.

    redline race-plan race.gpx --pace 5:30 --aid "12:Le Camp" --aid "26:Soucieu" --stop 2.5 \\
        --start 2030-11-30T23:00 --carbs 60

`--pace` is the flat-equivalent pace held over the whole race: the level is an input, never
guessed. The grade curve and the fuelling rule are the instance's when VAULT_PATH is set,
else Strava 2017 both ways and the engine's rulebook. Elevation as for `redline
route-profile`. The report itself is assembled by `redline.analysis.race_report`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from redline.coach.fr import route as text
from redline.integrations import weather
from redline.model import entrypoint, instance, paths, rulebook
from redline.model.errors import ConfigError, InputError
from redline.model.vocabulary import ElevationSource


def _clock(hours: float) -> str:
    minutes = round(hours * 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


def _weather(forecast) -> str:
    return text.WEATHER.format(temp=forecast.temp_c, feels=forecast.feels_like_c,
                               wind=forecast.wind_kmh, precip=forecast.precip_mm_h)


def _first_upper(sentence: str) -> str:
    return sentence[:1].upper() + sentence[1:]


def _pace(seconds: float) -> str:
    return f"{int(seconds // 60)}:{round(seconds % 60):02d}/km"


def _zone(lat: float, lon: float, naive: dt.datetime) -> dt.datetime:
    return weather.resolve_offset(lat, lon, naive)[0]


def render(report) -> str:
    from redline.analysis import route_profile

    median, p90 = report.model_error
    lines = [text.PLAN_HEADER.format(name=report.name or "?", length=report.length_km,
                                     flat=report.flat_equivalent_km,
                                     pace=_pace(report.pace_s_per_km), stop=report.stop_minutes),
             _first_upper(text.SOURCE[report.source].format(sigma=route_profile.SIGMA_M)),
             text.CURVE_DECLARED if report.curve_declared else text.CURVE_DEFAULT, ""]
    for passage in report.passages:
        if passage is report.finish:
            lines.append(text.FINISH.format(
                km=passage.km, time=_clock(passage.hours), low=_clock(passage.low_hours),
                high=_clock(passage.high_hours), low90=_clock(report.finish_p90_hours[0]),
                high90=_clock(report.finish_p90_hours[1])))
        else:
            lines.append(text.PASSAGE.format(
                km=passage.km, name=passage.name, time=_clock(passage.hours),
                low=_clock(passage.low_hours), high=_clock(passage.high_hours)))
        if passage.clock is not None:
            lines.append(text.CLOCK.format(clock=passage.clock.strftime("%H:%M"))
                         + (" · " + _weather(passage.forecast) if passage.forecast else ""))
        if passage.carbs_g is not None:
            lines.append(text.FUEL.format(grams=passage.carbs_g,
                                          duration=_clock(passage.running_hours)))
    if report.has_fast_bound:
        lines.append(text.FAST_BOUND.format(time=_clock(report.finish.fast_hours)))
    lines.append("")
    if report.forecast_available is False:
        lines.append(text.WEATHER_LATER)
    if report.carbs_g_h:
        lines.append(text.FUEL_HEADER.format(carbs=report.carbs_g_h,
                                             mix=text.FUEL_MIX if report.carbs_mixed else ""))
    lines.extend([text.BAND.format(median=median * 100, p90=p90 * 100), text.NOT_MODELLED])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gpx", type=Path, help="the race route, as a GPX file")
    parser.add_argument("--pace", required=True, help="flat-equivalent pace held, M:SS per km")
    parser.add_argument("--aid", action="append", default=[],
                        help="an aid station, KM:NAME (repeatable)")
    parser.add_argument("--stop", type=float, default=0.0,
                        help="minutes stopped at each aid station")
    parser.add_argument("--elevation", choices=tuple(ElevationSource), default="ign")
    parser.add_argument("--start", help="local start time, YYYY-MM-DDTHH:MM: clock times, "
                                        "and the forecast at each passage once it exists")
    parser.add_argument("--carbs", type=int,
                        help="carbohydrate per hour of running, in g; capped by rule f1")
    parser.add_argument("--json", action="store_true", help="machine output")
    args = parser.parse_args()
    try:
        from redline.analysis import race_pace, race_report, route_profile
    except ImportError as exc:
        raise ConfigError("the race plan needs the `route` extra: "
                          "pip install -e '.[route]'") from exc
    from redline.integrations import ign_altimetry

    declared = None
    book = rulebook.parse(rulebook.DEFAULT.read_text(encoding="utf-8"), rulebook.DEFAULT)
    if os.environ.get("VAULT_PATH", "").strip():
        declared = instance.load(paths.vault_root())
        book = rulebook.load(paths.vault_root())
    start = None
    if args.start:
        try:
            start = dt.datetime.fromisoformat(args.start)
        except ValueError:
            raise InputError(f"--start {args.start!r}: expected YYYY-MM-DDTHH:MM") from None
    report = race_report.build(
        route_profile.read_gpx(args.gpx), pace_s_per_km=race_pace.parse_pace(args.pace),
        aids=[race_pace.parse_aid(item) for item in args.aid], stop_minutes=args.stop,
        curve=race_pace.GradeCurve(declared.descent_speed, declared.training_climb_speed)
        if declared else race_report.STRAVA_ONLY,
        sampler=ign_altimetry.elevations if args.elevation == ElevationSource.IGN else None,
        carbs_g_h=args.carbs, carbs_cap_g_h=book.parameters.carbs_cap_g_h,
        carbs_mix_above_g_h=book.parameters.carbs_mix_above_g_h,
        start_local=start, zone=declared.timezone if declared else None,
        resolve_zone=_zone, forecaster=weather.get_weather)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) if args.json
          else render(report))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
