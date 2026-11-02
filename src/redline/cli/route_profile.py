"""The `redline route-profile` command: the elevation profile of a race route.

    redline route-profile race.gpx --at 12 --at 26 --at 33
    redline route-profile race.gpx --elevation gpx --json

By default the elevation comes from the IGN terrain model: the route's coordinates are sent
to the Géoplateforme altimetry service. `--elevation gpx` stays offline and reads the GPX.
`--at` cuts the route at these kilometres (aid stations) and gives the climb of each stretch.
"""

from __future__ import annotations

import argparse
import json
from itertools import pairwise
from pathlib import Path

from redline.coach.fr import route as text
from redline.model import entrypoint
from redline.model.errors import ConfigError
from redline.model.vocabulary import ElevationSource


def _steepest(climb) -> tuple[float, float]:
    """(grade, length) of a climb's steepest section."""
    section = max(climb.sections, key=lambda item: item.grade_pct)
    return section.grade_pct, section.end_m - section.start_m


def climb_lines(found: list[tuple], clock=None) -> list[str]:
    """The main climbs, one line each, then the count of the bumps. Each entry is
    (start km, end km, gain, grade, steepest grade, its length, main, hours to the foot);
    `clock` writes the hours when they are known."""
    main = [entry for entry in found if entry[6]]
    bumps = [entry for entry in found if not entry[6]]
    if not found:
        return []
    lines = ["", text.CLIMBS_HEADER.format(main=len(main), bumps=len(bumps),
                                           bump_gain=sum(entry[2] for entry in bumps))]
    for start, end, gain, grade, steepest, steepest_m, _, hours in main:
        line = text.CLIMB.format(start=start, end=end, gain=gain, length=end - start,
                                 grade=grade, steepest=steepest, steepest_m=steepest_m)
        if clock is not None and hours is not None:
            line += text.CLIMB_FOOT.format(time=clock(hours))
        lines.append(line)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gpx", type=Path, help="the race route, as a GPX file")
    parser.add_argument("--elevation", choices=tuple(ElevationSource), default="ign",
                        help="ign: IGN terrain model, GPX elevation outside its coverage; "
                             "gpx: the GPX elevation, offline")
    parser.add_argument("--at", type=float, action="append", default=[],
                        help="a kilometre to cut the route at (repeatable)")
    parser.add_argument("--json", action="store_true", help="machine output")
    args = parser.parse_args()
    try:
        from redline.analysis import climbs, route_profile
    except ImportError as exc:
        raise ConfigError("the route profile needs the `route` extra: "
                          "pip install -e '.[route]'") from exc
    from redline.integrations import ign_altimetry

    sampler = ign_altimetry.elevations if args.elevation == ElevationSource.IGN else None
    profile = route_profile.profile(route_profile.read_gpx(args.gpx), sampler)
    marks = [0.0, *sorted(km for km in args.at if 0 < km < profile.length_km),
             profile.length_km]
    stretches = [(start, end, *profile.climb(start, end)) for start, end in pairwise(marks)]
    high, high_km = profile.highest()
    found = climbs.detect(profile.distance_m, profile.elevation_m)
    if args.json:
        print(json.dumps({
            "name": profile.name, "source": profile.source.value,
            "length_km": round(profile.length_km, 2),
            "dplus_m": round(profile.dplus_m), "dminus_m": round(profile.dminus_m),
            "dplus_band_m": [round(v) for v in profile.dplus_band_m] if profile.dplus_band_m else None,
            "dminus_band_m": [round(v) for v in profile.dminus_band_m] if profile.dminus_band_m else None,
            "high_m": round(high), "high_km": round(high_km, 2),
            "stretches": [{"from_km": round(a, 2), "to_km": round(b, 2), "dplus_m": round(up),
                           "dminus_m": round(down)} for a, b, up, down in stretches],
            "climbs": [{"start_km": round(climb.start_m / 1000, 2),
                        "end_km": round(climb.end_m / 1000, 2), "gain_m": round(climb.gain_m),
                        "grade_pct": round(climb.grade_pct, 1), "main": climb.main,
                        "sections": [{"start_km": round(section.start_m / 1000, 2),
                                      "end_km": round(section.end_m / 1000, 2),
                                      "grade_pct": round(section.grade_pct, 1)}
                                     for section in climb.sections]}
                       for climb in found],
        }, ensure_ascii=False, indent=2))
        return 0
    lines = [text.HEADER.format(name=profile.name or "?", length=profile.length_km,
                                source=text.SOURCE[profile.source].format(sigma=route_profile.SIGMA_M))]
    if profile.dplus_band_m and profile.dminus_band_m:
        lines.append(text.TOTALS_BAND.format(
            dplus=profile.dplus_m, dplus_low=profile.dplus_band_m[0],
            dplus_high=profile.dplus_band_m[1], dminus=profile.dminus_m,
            dminus_low=profile.dminus_band_m[0], dminus_high=profile.dminus_band_m[1]))
    else:
        lines.append(text.TOTALS.format(dplus=profile.dplus_m, dminus=profile.dminus_m))
    lines.append(text.HIGH_POINT.format(high=high, high_km=high_km))
    lines.extend(text.STRETCH.format(start=a, end=b, dplus=up, dminus=down)
                 for a, b, up, down in stretches)
    lines.extend(climb_lines([(climb.start_m / 1000, climb.end_m / 1000, climb.gain_m,
                               climb.grade_pct, *_steepest(climb), climb.main, None)
                              for climb in found]))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
