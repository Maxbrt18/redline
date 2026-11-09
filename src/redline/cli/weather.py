"""The `redline weather` command: see `redline.integrations.weather`."""

from __future__ import annotations

import argparse
import json

from redline.coach.fr import weather as wording
from redline.coach.fr.weather import FLAGS, sky
from redline.integrations import weather
from redline.model import entrypoint
from redline.model.errors import InputError


def format_summary(w, when, midpoint=None):
    parts = [sky(w)]
    if w["temp"] is not None:
        s = wording.TEMPERATURE.format(temp=w["temp"])
        if w["feels_like"] is not None and abs(w["feels_like"] - w["temp"]) >= 1:
            s += wording.FEELS_LIKE.format(feels_like=w["feels_like"])
        parts.append(s)
    if w["humidity"] is not None:
        parts.append(wording.HUMIDITY.format(humidity=w["humidity"]))
    if w["wind"] is not None:
        parts.append(wording.WIND.format(wind=w["wind"]))
    if w["precip"] is not None and w["precip"] > 0:
        parts.append(wording.PRECIPITATION.format(precip=w["precip"]))
    if w.get("wbgt") is not None:
        parts.append(wording.WBGT.format(wbgt=w["wbgt"], flag=FLAGS[w["band"]]))
    local = when.strftime("%Y-%m-%d %H:%M %Z")
    out = wording.AT_START.format(local=local, parts=", ".join(parts))

    # The start is only one point. When the middle of the route differs markedly
    # (hillsides to town, valley floor to ridge), the start weather no longer
    # describes the session and would be wrongly quoted as the weather of the run.
    if midpoint and midpoint["temp"] is not None and w["temp"] is not None:
        gap = midpoint["temp"] - w["temp"]
        if abs(gap) >= weather.MIDPOINT_GAP_C:
            out += wording.MIDPOINT.format(temp=midpoint["temp"], gap=gap)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="The weather of a run, from Open-Meteo.")
    ap.add_argument("--polyline", help="Strava encoded polyline (summary_polyline).")
    ap.add_argument("--lat", type=float, help="Start latitude.")
    ap.add_argument("--lon", type=float, help="Start longitude.")
    ap.add_argument("--time", required=True,
                    help="ISO 8601 start time. With --local: Strava's "
                         "`start_local` as is, without a zone.")
    ap.add_argument("--local", action="store_true",
                    help="--time is the wall-clock time at the start "
                         "(= `start_local` of redline strava). The offset is "
                         "resolved from the coordinates. Never add a `Z` "
                         "instead.")
    ap.add_argument("--json", action="store_true", help="Raw JSON output.")
    args = ap.parse_args()

    midpoint_point = None
    if args.polyline:
        try:
            pts = weather.decode_polyline(args.polyline)
        except ValueError as exc:
            raise InputError(str(exc)) from exc
        if not pts:
            raise InputError("Empty or unreadable polyline.")
        lat, lon = pts[0]
        if len(pts) >= 3:
            midpoint_point = pts[len(pts) // 2]
    elif args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        raise InputError("Give --polyline, or --lat and --lon.")

    try:
        when = weather.parse_time(args.time, local=args.local)
    except ValueError as exc:
        raise InputError(str(exc)) from exc

    zone_name = None
    if args.local:
        try:
            when, zone_name = weather.resolve_offset(lat, lon, when)
        except weather.WeatherError as exc:
            raise weather.WeatherError(f"Time zone of the start cannot be determined: {exc}") from exc

    try:
        w = weather.get_weather(lat, lon, when)
    except weather.WeatherError as exc:
        raise weather.WeatherError(f"Weather unavailable: {exc}") from exc

    midpoint = None
    if midpoint_point:
        try:
            midpoint = weather.get_weather(midpoint_point[0], midpoint_point[1], when)
        except weather.WeatherError:
            # The second point is a convenience, not the data: its failure must never
            # deprive the coach of the start weather.
            midpoint = None

    if args.json:
        payload = {"lat": lat, "lon": lon, "timezone": zone_name, **w, "conditions": sky(w)}
        if midpoint and midpoint_point:
            payload["midpoint"] = {"lat": midpoint_point[0], "lon": midpoint_point[1], **midpoint,
                                   "conditions": sky(midpoint)}
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(format_summary(w, when, midpoint))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
