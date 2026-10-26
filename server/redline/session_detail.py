"""Le détail d'une séance faite, pour le carnet (maquette v2 validée le 2026-10-26).

Deux moitiés. Les relevés : les flux de la sortie et ses tours avec leurs bornes, puis la
météo horaire au départ, demandés aux fournisseurs au premier affichage et gardés avec la
capture Strava (`store_strava_detail`). Les calculs : des fonctions pures sur ces flux,
qui rendent des faits (temps par zone, kilomètres, répétitions, arrêts, dérive). La page
met en forme ; aucun chiffre n'y est compté.

Un flux se lit échantillon par échantillon : l'échantillon `k` couvre l'intervalle qui le
précède. Un intervalle de plus de `GAP_S` secondes est un arrêt (la montre en pause) ;
un échantillon sous `MOVING_MPS` ne compte pas dans le temps en mouvement.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from scripts import strava, weather
from scripts.model import debrief, hr_zones

log = logging.getLogger("redline")

STREAM_KEYS = "time,heartrate,altitude,distance,velocity_smooth,cadence"
GAP_S = 10
MOVING_MPS = 0.8
#: Un kilomètre entamé de moins que ça ne fait pas une ligne : il rejoint le précédent.
SPLIT_MIN_M = 300
#: Sous ce dénivelé, le profil d'altitude n'apprend rien : il n'est pas montré.
PROFILE_MIN_DPLUS_M = 50
#: La dérive cardiaque ne se lit que sur une sortie assez longue.
DRIFT_MIN_S = 30 * 60
#: Le graphique n'a pas besoin de plus de points que l'écran n'a de pixels.
CHART_POINTS = 240
#: Un arrêt plus court ne se signale pas sur le graphique.
STOP_MARK_S = 60
HR_DROP_S = 60


# ------------------------------------------------------------ relevés
def compact(raw: dict, laps: list) -> dict:
    """Les flux Strava allégés (entiers ou arrondis) et les bornes de chaque tour."""
    def data(key: str) -> list:
        stream = raw.get(key) or {}
        values = stream.get("data") if isinstance(stream, dict) else None
        return values if isinstance(values, list) else []

    time = [int(value) for value in data("time")]
    if not time:
        raise ValueError("flux de temps absent")
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
    """Les bornes [début, fin] de chaque tour, en secondes écoulées depuis le départ.

    Lues sur l'heure de départ et la durée écoulée de chaque tour. Les `start_index` de
    Strava ne comptent pas dans la même base que les flux : sur le seuil du 2026-10-26,
    ils plaçaient la première répétition 168 s trop tôt, sur le plat au lieu de la côte.
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


def weather_at(meteo: dict) -> dict:
    """La météo horaire au point et à l'heure de départ (WBGT compris s'il converge)."""
    points = weather.decode_polyline(meteo["polyline"])
    if not points:
        raise ValueError("polyline vide")
    lat, lon = points[0]
    local = weather.parse_time(meteo["time"], local=True)
    moment, _ = weather.resolve_offset(lat, lon, local)
    return weather.get_weather(lat, lon, moment)


def ensure(store: Any, intake: dict) -> dict:
    """Relève ce qui manque encore à la capture, sans jamais bloquer le détail.

    Une panne du fournisseur laisse le champ vide : la page montre la séance sans son
    graphique ou sans sa météo, et le prochain affichage réessaie.
    """
    activity_id = intake["activity_id"]
    streams, conditions = intake.get("streams"), intake.get("weather")
    fresh: dict[str, dict] = {}
    # Des flux gardés avant les bornes horodatées (`lap_times`) se relèvent une fois.
    if streams is None or "lap_times" not in streams:
        try:
            streams = fresh["streams"] = fetch_streams(activity_id)
        except Exception as exc:
            log.warning("Flux Strava de %s non relevés : %s", activity_id, exc)
    meteo = (intake.get("distilled") or {}).get("meteo") or {}
    if conditions is None and meteo.get("polyline") and meteo.get("time"):
        try:
            conditions = fresh["weather"] = weather_at(meteo)
        except Exception as exc:
            log.warning("Météo de %s non relevée : %s", activity_id, exc)
    if fresh:
        store.store_strava_detail(activity_id, **fresh)
    return {**intake, "streams": streams, "weather": conditions}


# ------------------------------------------------------------ lectures d'un flux
def _steps(s: dict):
    """(k, durée de l'intervalle, en mouvement ?) pour chaque échantillon après le premier."""
    time, speed = s["t"], s.get("v")
    for k in range(1, len(time)):
        dt = time[k] - time[k - 1]
        moving = dt <= GAP_S and (speed is None or (speed[k] or 0) >= MOVING_MPS)
        yield k, dt, moving


def zone_seconds(s: dict, limits: tuple[int, ...] | None = None) -> list[int] | None:
    """Le temps passé dans chaque zone (Z1 à Z5), arrêts exclus."""
    hr = s.get("hr")
    if not hr:
        return None
    totals = [0] * 5
    for k, dt, _ in _steps(s):
        if dt <= GAP_S and hr[k]:
            totals[hr_zones.zone(hr[k], limits) - 1] += dt
    return totals


def stops(s: dict) -> list[list[int]]:
    """Les arrêts de la montre : [début, fin] en secondes écoulées."""
    return [[s["t"][k - 1], s["t"][k]] for k, dt, _ in _steps(s) if dt > GAP_S]


def stop_summary(s: dict) -> dict | None:
    """Les arrêts en un chiffre : combien, combien de temps, et le plus long au km près."""
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
    """Chaque kilomètre : distance, temps en mouvement, FC et cadence moyennes, D+."""
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
    """La FC moyenne des deux moitiés du temps en mouvement."""
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
    """Les courbes du graphique : moyennes par tranche de temps, `None` pendant un arrêt."""
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

    # Chaque point au milieu de sa tranche : il en porte la moyenne.
    return {"t": [round((index + 0.5) * width) for index in range(len(buckets))], "duration": duration,
            "hr": line(hr), "alt": line(alt, 1)}


def _at(s: dict, second: float) -> int | None:
    """La FC de l'échantillon le plus proche d'un instant."""
    hr = s.get("hr")
    if not hr:
        return None
    k = min(range(len(s["t"])), key=lambda index: abs(s["t"][index] - second))
    return hr[k]


# ------------------------------------------------------------ séance de qualité
def _lap_bounds(laps: list, s: dict | None) -> list[list[float]]:
    """Les bornes [début, fin] de chaque tour : horodatées, sinon les durées cumulées."""
    if s and len(s.get("lap_times") or []) == len(laps):
        return s["lap_times"]
    out, clock = [], 0.0
    for lap in laps:
        seconds = debrief._seconds(lap.get("temps")) or 0
        out.append([clock, clock + seconds])
        clock += seconds
    return out


def quality(laps: list, s: dict | None) -> dict | None:
    """Les phases d'une séance de qualité et le détail de chaque répétition.

    Les blocs viennent de la même lecture des tours que la carte des répétitions
    (`debrief.lap_blocks`) ; échauffement = avant la première, retour au calme = après
    la dernière.
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
            pairs = [(item.get(key), phase["time_s"]) for item, phase in zip(items, block, strict=True)]
            if not seconds or any(value is None for value, _ in pairs):
                return None
            return sum(value * weight for value, weight in pairs) / seconds

        beat, step = weighted("fc_moy"), weighted("cadence_pas_min")
        return {"time_s": seconds, "dist_m": round(metres),
                "pace_s": round(seconds * 1000 / metres) if metres else None,
                "hr": None if beat is None else round(beat),
                "cadence": None if step is None else round(step),
                "dplus": round(sum(item.get("dplus_m") or 0 for item in items))}

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
        # Plus vite que toutes les précédentes : la dernière a accéléré (garde-fou n°6).
        reading = {"last": paces[-1], "fastest_before": min(before), "slowest_before": max(before),
                   "final_acceleration": paces[-1] < min(before)}
    return {"phases": phases, "reps": reps, "reading": reading}
