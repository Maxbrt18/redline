"""The detail of a completed session, for the notebook.

Two halves. The readings: the run's streams and its laps with their bounds, then the
hourly weather at the start, requested from the providers on first display and kept with
the Strava capture (`store_strava_detail`). The computations: pure functions over those
streams that return facts (time per zone, kilometres, repetitions, stops, drift). The
page formats; no figure is computed there.

A stream is read sample by sample: sample `k` covers the interval that precedes it. An
interval longer than `GAP_S` seconds is a stop (the watch paused); a sample below
`MOVING_MPS` does not count toward moving time.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from redline.integrations import strava, weather
from redline.model import debrief, hr_zones
from redline.model.activity import Activity, Lap

log = logging.getLogger("redline")

STREAM_KEYS = "time,heartrate,altitude,distance,velocity_smooth,cadence"
GAP_S = 10
MOVING_MPS = 0.8
#: A kilometre started with less than this is not a row: it merges into the previous one.
SPLIT_MIN_M = 300
#: Below this elevation gain the altitude profile tells nothing: it is not shown.
PROFILE_MIN_DPLUS_M = 50
#: Cardiac drift is only readable on a long enough run.
DRIFT_MIN_S = 30 * 60
#: The chart needs no more points than the screen has pixels.
CHART_POINTS = 240
#: A shorter stop is not marked on the chart.
STOP_MARK_S = 60
HR_DROP_S = 60


# ------------------------------------------------------------ readings
def compact(raw: dict, laps: list) -> dict:
    """Slimmed-down Strava streams (integers or rounded) and the bounds of each lap."""
    def data(key: str) -> list:
        stream = raw.get(key) or {}
        values = stream.get("data") if isinstance(stream, dict) else None
        return values if isinstance(values, list) else []

    time = [int(value) for value in data("time")]
    if not time:
        raise ValueError("no time stream")
    size = len(time)

    def aligned(key: str, digits: int | None) -> list | None:
        values = data(key)
        if len(values) != size:
            return None
        return [None if value is None else round(value) if digits is None else round(value, digits)
                for value in values]

    return {"t": time, "hr": aligned("heartrate", None), "alt": aligned("altitude", 1),
            "d": aligned("distance", 1), "v": aligned("velocity_smooth", 2),
            "cad": aligned("cadence", None), "lap_times": lap_times(laps)}


def lap_times(laps: list) -> list[list[int]]:
    """The [start, end] bounds of each lap, in seconds elapsed since the start.

    Read from each lap's start time and elapsed duration. Strava's `start_index` values
    are not on the same basis as the streams and would place laps too early.
    """
    def instant(lap: dict) -> datetime | None:
        try:
            return datetime.fromisoformat(str(lap["start_date"]))
        except (KeyError, ValueError):
            return None

    starts = [instant(lap) for lap in laps or []]
    known = [start for start in starts if start is not None]
    if not known or len(known) != len(starts):
        return []
    out = []
    for lap, start in zip(laps, known, strict=True):
        offset = round((start - known[0]).total_seconds())
        out.append([offset, offset + int(lap.get("elapsed_time") or 0)])
    return out


def fetch_streams(activity_id: int) -> dict:
    raw = strava.call(f"/activities/{int(activity_id)}/streams",
                      {"keys": STREAM_KEYS, "key_by_type": "true"})
    laps = strava.call(f"/activities/{int(activity_id)}/laps")
    return compact(raw, laps if isinstance(laps, list) else [])


def weather_at(query: dict) -> dict:
    """Hourly weather at the start point and time (WBGT included when it converges)."""
    points = weather.decode_polyline(query["polyline"])
    if not points:
        raise ValueError("empty polyline")
    lat, lon = points[0]
    local = weather.parse_time(query["time"], local=True)
    moment, _ = weather.resolve_offset(lat, lon, local)
    return weather.get_weather(lat, lon, moment)


def ensure(store: Any, intake: dict) -> dict:
    """Fetch what the capture still lacks, never blocking the detail.

    A provider outage leaves the field empty: the page shows the session without its
    chart or weather, and the next display retries.
    """
    activity_id = intake["activity_id"]
    streams, conditions = intake.get("streams"), intake.get("weather")
    fresh: dict[str, dict] = {}
    # Streams kept before the timestamped bounds (`lap_times`) are fetched again once.
    if streams is None or "lap_times" not in streams:
        try:
            streams = fresh["streams"] = fetch_streams(activity_id)
        except Exception as exc:
            log.warning("Strava streams of %s not fetched: %s", activity_id, exc)
    stored = intake.get("distilled")
    query = Activity.from_json(stored).weather_query if stored else None
    if conditions is None and query is not None:
        try:
            conditions = fresh["weather"] = weather_at(query)
        except Exception as exc:
            log.warning("Weather of %s not fetched: %s", activity_id, exc)
    if fresh:
        store.store_strava_detail(activity_id, **fresh)
    return {**intake, "streams": streams, "weather": conditions}


# ------------------------------------------------------------ stream readings
def _steps(s: dict):
    """(k, interval duration, moving?) for each sample after the first."""
    time, speed = s["t"], s.get("v")
    for k in range(1, len(time)):
        dt = time[k] - time[k - 1]
        moving = dt <= GAP_S and (speed is None or (speed[k] or 0) >= MOVING_MPS)
        yield k, dt, moving


def zone_seconds(s: dict, limits: tuple[int, ...]) -> list[int] | None:
    """Time spent in each zone (Z1 to Z5), stops excluded."""
    hr = s.get("hr")
    if not hr:
        return None
    totals = [0] * 5
    for k, dt, _ in _steps(s):
        if dt <= GAP_S and hr[k]:
            totals[hr_zones.zone(hr[k], limits) - 1] += dt
    return totals


def stops(s: dict) -> list[list[int]]:
    """The watch stops: [start, end] in elapsed seconds."""
    return [[s["t"][k - 1], s["t"][k]] for k, dt, _ in _steps(s) if dt > GAP_S]


def stop_summary(s: dict) -> dict | None:
    """Stops in one figure: how many, how long, and the longest one to the nearest km."""
    found = [(k, dt) for k, dt, _ in _steps(s) if dt > GAP_S]
    if not found:
        return None
    k, longest = max(found, key=lambda item: item[1])
    distance = s.get("d")
    return {"count": len(found), "total_s": sum(dt for _, dt in found), "longest_s": longest,
            "longest_km": int((distance[k] or 0) // 1000) + 1 if distance else None}


def _mean(values: list) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None


def km_splits(s: dict) -> list[dict] | None:
    """Each kilometre: distance, moving time, mean HR and cadence, elevation gain."""
    distance = s.get("d")
    if not distance:
        return None
    total, hr, alt, cad = distance[-1] or 0, s.get("hr"), s.get("alt"), s.get("cad")
    edges = [float(km * 1000) for km in range(1, int(total // 1000) + 1)]
    if edges and total - edges[-1] < SPLIT_MIN_M:
        edges.pop()
    edges.append(float(total))
    out: list[dict] = []
    start, k0 = 0.0, 0
    for edge in edges:
        moving_s, hrs, cads, gain, k = 0, [], [], 0.0, k0
        while k + 1 < len(distance) and (distance[k + 1] or 0) <= edge:
            k += 1
        for j in range(k0 + 1, k + 1):
            dt = s["t"][j] - s["t"][j - 1]
            if dt <= GAP_S and (s.get("v") is None or (s["v"][j] or 0) >= MOVING_MPS):
                moving_s += dt
                if hr:
                    hrs.append(hr[j])
                if cad:
                    cads.append(cad[j])
            if alt and alt[j] is not None and alt[j - 1] is not None and alt[j] > alt[j - 1]:
                gain += alt[j] - alt[j - 1]
        span = edge - start
        if span > 0 and moving_s > 0:
            beat, step = _mean(hrs), _mean(cads)
            out.append({"n": len(out) + 1, "dist_m": round(span), "moving_s": moving_s,
                        "pace_s": round(moving_s * 1000 / span),
                        "hr": None if beat is None else round(beat),
                        "cadence": None if step is None else round(2 * step),
                        "dplus": round(gain)})
        start, k0 = edge, k
    return out


def drift(s: dict) -> dict | None:
    """Mean HR of the two halves of the moving time."""
    hr = s.get("hr")
    if not hr:
        return None
    moving = [k for k, _, ok in _steps(s) if ok and hr[k]]
    if len(moving) < 2 or s["t"][moving[-1]] - s["t"][moving[0]] < DRIFT_MIN_S:
        return None
    half = len(moving) // 2
    return {"first": round(_mean([hr[k] for k in moving[:half]]) or 0),
            "second": round(_mean([hr[k] for k in moving[half:]]) or 0)}


def series(s: dict, points: int = CHART_POINTS) -> dict:
    """The chart curves: means per time slice, `None` during a stop."""
    time, hr, alt = s["t"], s.get("hr"), s.get("alt")
    duration = time[-1] or 1
    width = max(1.0, duration / points)
    buckets: list[list[int]] = [[] for _ in range(int(duration // width) + 1)]
    for k in range(len(time)):
        buckets[min(len(buckets) - 1, int(time[k] // width))].append(k)

    def line(values: list | None, digits: int = 0) -> list | None:
        if not values:
            return None
        out = []
        for bucket in buckets:
            mean = _mean([values[k] for k in bucket])
            out.append(None if mean is None else round(mean, digits) if digits else round(mean))
        return out

    # Each point sits at the middle of its slice and carries its mean.
    return {"t": [round((index + 0.5) * width) for index in range(len(buckets))], "duration": duration,
            "hr": line(hr), "alt": line(alt, 1)}


def _at(s: dict, second: float) -> int | None:
    """HR of the sample closest to a given instant."""
    hr = s.get("hr")
    if not hr:
        return None
    k = min(range(len(s["t"])), key=lambda index: abs(s["t"][index] - second))
    return hr[k]


# ------------------------------------------------------------ quality session
def _lap_bounds(laps: Sequence[Lap], s: dict | None) -> list[list[float]]:
    """The [start, end] bounds of each lap: timestamped, else the cumulated durations."""
    if s and len(s.get("lap_times") or []) == len(laps):
        return s["lap_times"]
    out, clock = [], 0.0
    for lap in laps:
        seconds = lap.moving_s
        out.append([clock, clock + seconds])
        clock += seconds
    return out


def quality(laps: Sequence[Lap], s: dict | None) -> dict | None:
    """The phases of a quality session and the detail of each repetition.

    Blocks come from the same lap reading as the repetitions card
    (`debrief.lap_blocks`); warm-up = before the first, cool-down = after the last.
    """
    blocks = debrief.lap_blocks(laps)
    if blocks is None:
        return None
    bounds = _lap_bounds(laps, s)
    efforts = [index for index, (fast, _) in enumerate(blocks) if fast]

    def span(block: list[dict]) -> tuple[float, float]:
        return bounds[block[0]["index"]][0], bounds[block[-1]["index"]][1]

    def merged(block: list[dict]) -> dict:
        items = [laps[phase["index"]] for phase in block]
        seconds = sum(phase["time_s"] for phase in block)
        metres = sum(phase["dist_m"] for phase in block)

        def weighted(key: str) -> float | None:
            pairs = [(getattr(item, key), phase["time_s"])
                     for item, phase in zip(items, block, strict=True)]
            if not seconds or any(value is None for value, _ in pairs):
                return None
            return sum(value * weight for value, weight in pairs) / seconds

        beat, step = weighted("hr_avg_bpm"), weighted("cadence_spm")
        return {"time_s": seconds, "dist_m": round(metres),
                "pace_s": round(seconds * 1000 / metres) if metres else None,
                "hr": None if beat is None else round(beat),
                "cadence": None if step is None else round(step),
                "dplus": round(sum(item.elevation_gain_m or 0 for item in items))}

    phases: list[dict] = []
    reps: list[dict] = []
    first, last = efforts[0], efforts[-1]
    if first > 0:
        start, end = bounds[blocks[0][1][0]["index"]][0], span(blocks[first - 1][1])[1]
        phases.append({"kind": "warmup", "start": start, "end": end,
                       **merged([phase for _, block in blocks[:first] for phase in block])})
    for fast, block in blocks[first:last + 1]:
        start, end = span(block)
        phase = {"kind": "rep" if fast else "recovery", "start": start, "end": end, **merged(block)}
        phases.append(phase)
        if fast:
            after = _at(s, end + HR_DROP_S) if s else None
            at_end = _at(s, end) if s else None
            reps.append({**phase, "n": len(reps) + 1,
                         "hr_drop": None if after is None or at_end is None else at_end - after})
    if last + 1 < len(blocks):
        tail = [phase for _, block in blocks[last + 1:] for phase in block]
        phases.append({"kind": "cooldown", "start": span(blocks[last + 1][1])[0],
                       "end": span(blocks[-1][1])[1], **merged(tail)})
    paces = [rep["pace_s"] for rep in reps if rep["pace_s"]]
    reading = None
    if len(paces) >= 2:
        before = paces[:-1]
        # Faster than all the previous ones: the last repetition accelerated.
        reading = {"last": paces[-1], "fastest_before": min(before), "slowest_before": max(before),
                   "final_acceleration": paces[-1] < min(before)}
    return {"phases": phases, "reps": reps, "reading": reading}
