"""Stored Strava activities take the shape of `scripts/model/activity.py`.

`strava_activity_intakes.distilled` held French keys and values formatted for reading
(`temps_mouvement: "42:10"`, `allure_min_km: "5:55"`). It now holds `Activity.to_json()`:
English keys, durations in seconds, distances in metres, and no derived value. Pace,
speed and the lap count are computed on read, so they are dropped here and recomputed
by the downgrade. The old rows kept distances to the metre only, so a pace recomputed
from them can differ by one second per kilometre from the text they stored.

A capture that failed stores `{}` and is left alone. A key this migration does not
know stops it rather than being dropped.

Revision ID: 20261028_0005
Revises: 20261028_0004
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261028_0005"
down_revision = "20261028_0004"
branch_labels = None
depends_on = None

SCHEMA = "redline"
FOOT_SPORTS = frozenset({"Run", "TrailRun", "VirtualRun", "Walk", "Hike"})

#: Old key -> new key, for values that keep their unit.
RENAMED = {
    "id": "id", "sport": "sport", "nom": "name", "description": "description",
    "depart_local": "start_local", "dplus_m": "elevation_gain_m",
    "fc_moy": "hr_avg_bpm", "fc_max": "hr_max_bpm",
    "cadence_pas_min": "cadence_spm", "cadence_tr_min": "cadence_rpm",
    "effort_relatif": "relative_effort", "calories": "calories",
    "materiel_id": "gear_id", "nb_athletes": "athlete_count",
}
LAP_RENAMED = {
    "nom": "name", "fc_moy": "hr_avg_bpm", "fc_max": "hr_max_bpm",
    "dplus_m": "elevation_gain_m", "cadence_pas_min": "cadence_spm",
    "cadence_tr_min": "cadence_rpm",
}
#: Old keys computed from others, dropped by the upgrade.
DERIVED = {"allure_min_km", "vitesse_kmh", "nb_laps"}
LAP_DERIVED = {"rep", "allure_min_km"}

ACTIVITY_FIELDS = (
    "id", "sport", "name", "description", "start_local", "distance_m", "moving_s",
    "elapsed_s", "elevation_gain_m", "hr_avg_bpm", "hr_max_bpm", "cadence_spm",
    "cadence_rpm", "relative_effort", "calories", "gear_id", "athlete_count",
    "route_polyline", "laps",
)
LAP_FIELDS = ("name", "distance_m", "moving_s", "hr_avg_bpm", "hr_max_bpm",
              "elevation_gain_m", "cadence_spm", "cadence_rpm")


def _seconds(text: str | None) -> int | None:
    if not text:
        return None
    total = 0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total


def _clock(seconds: int | None) -> str | None:
    if not seconds:
        return None
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _pace(distance_m: float, moving_s: int) -> str | None:
    if not distance_m or not moving_s:
        return None
    return _clock(round(moving_s / (distance_m / 1000))) or None


def _metres(km: Any) -> float:
    return round(float(km or 0) * 1000, 1)


def _unknown(row: dict, known: set[str], what: str) -> None:
    extra = set(row) - known
    if extra:
        raise RuntimeError(f"{what} : clés inconnues {sorted(extra)}")


def lap_to_english(lap: dict) -> dict:
    _unknown(lap, set(LAP_RENAMED) | LAP_DERIVED | {"distance_km", "temps"}, "tour")
    out: dict[str, Any] = dict.fromkeys(LAP_FIELDS)
    out.update({new: lap.get(old) for old, new in LAP_RENAMED.items()})
    out["distance_m"] = _metres(lap.get("distance_km"))
    out["moving_s"] = _seconds(lap.get("temps")) or 0
    return out


def to_english(old: dict) -> dict:
    known = set(RENAMED) | DERIVED | {
        "distance_km", "temps_mouvement", "temps_total", "meteo", "laps"}
    _unknown(old, known, f"activité {old.get('id')}")
    out: dict[str, Any] = dict.fromkeys(ACTIVITY_FIELDS)
    out.update({new: old.get(key) for key, new in RENAMED.items()})
    out["description"] = old.get("description") or ""
    out["distance_m"] = _metres(old.get("distance_km"))
    out["moving_s"] = _seconds(old.get("temps_mouvement")) or 0
    out["elapsed_s"] = _seconds(old.get("temps_total"))
    out["route_polyline"] = (old.get("meteo") or {}).get("polyline")
    out["laps"] = [lap_to_english(lap) for lap in old.get("laps") or []]
    return out


def lap_to_french(lap: dict, index: int, on_foot: bool) -> dict:
    out: dict[str, Any] = {"rep": index, "nom": lap["name"],
                           "distance_km": round(lap["distance_m"] / 1000, 3),
                           "temps": _clock(lap["moving_s"]), "fc_moy": lap["hr_avg_bpm"],
                           "fc_max": lap["hr_max_bpm"], "dplus_m": lap["elevation_gain_m"]}
    if on_foot:
        out["allure_min_km"] = _pace(lap["distance_m"], lap["moving_s"])
    for old, new in (("cadence_pas_min", "cadence_spm"), ("cadence_tr_min", "cadence_rpm")):
        if lap[new] is not None:
            out[old] = lap[new]
    return out


def to_french(new: dict) -> dict:
    on_foot = new["sport"] in FOOT_SPORTS
    out: dict[str, Any] = {old: new[key] for old, key in RENAMED.items()
                           if not old.startswith("cadence_")}
    out["distance_km"] = round(new["distance_m"] / 1000, 3)
    out["temps_mouvement"] = _clock(new["moving_s"])
    out["temps_total"] = _clock(new["elapsed_s"])
    out["nb_laps"] = len(new["laps"])
    if on_foot:
        out["allure_min_km"] = _pace(new["distance_m"], new["moving_s"])
    elif new["distance_m"] and new["moving_s"]:
        out["vitesse_kmh"] = round(new["distance_m"] / new["moving_s"] * 3.6, 1)
    for old, key in (("cadence_pas_min", "cadence_spm"), ("cadence_tr_min", "cadence_rpm")):
        if new[key] is not None:
            out[old] = new[key]
    if new["route_polyline"]:
        out["meteo"] = {"polyline": new["route_polyline"], "time": new["start_local"]}
    out["laps"] = [lap_to_french(lap, index, on_foot)
                   for index, lap in enumerate(new["laps"], start=1)]
    return out


def _rewrite(convert, already: str) -> None:
    intakes = sa.table("strava_activity_intakes", sa.column("activity_id"),
                       sa.column("distilled", JSONB), schema=SCHEMA)
    bind = op.get_bind()
    for activity_id, stored in bind.execute(
            sa.select(intakes.c.activity_id, intakes.c.distilled)).all():
        if not stored or already in stored:
            continue
        bind.execute(intakes.update().where(intakes.c.activity_id == activity_id)
                     .values(distilled=convert(stored)))


def upgrade() -> None:
    _rewrite(to_english, already="moving_s")


def downgrade() -> None:
    _rewrite(to_french, already="nom")
