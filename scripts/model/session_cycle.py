"""Machine d'état pure d'une séance, indépendante du transport et du stockage.

Le programme dit ce qui doit avoir lieu. Ce module dit où en est cette séance dans
son cycle opérationnel. Les rappels et les débriefs se branchent sur ces états ; ils
ne les redéfinissent pas chacun de leur côté.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

TERMINAL_STATES = frozenset({"missed", "cancelled", "closed"})
# Un cycle dont l'exécution a déjà laissé une trace ne redevient jamais une simple
# intention parce que le programme est révisé ensuite.
OBSERVED_STATES = frozenset({"started", "completed"})
#: « Très légère » (0,5) depuis le 2026-10-11 : sa base habituelle est légère, et « mieux
#: que d'habitude » ou « très légère » n'est ni légère ni nulle (l'athlète, audit du 11/10).
MORNING_TRACES = ("aucune", "tres-legere", "legere", "nette")
MORNING_TRACE_VALUES = {"aucune": 0, "tres-legere": 0.5, "legere": 1, "nette": 2}
LOCAL_TIMEZONE = ZoneInfo("Europe/Paris")


def scheduled_for(day: str | dt.date, start_time: str | None) -> dt.datetime | None:
    """Compose la date du plan et son heure locale en instant non ambigu."""
    if not start_time:
        return None
    if isinstance(day, str):
        day = dt.date.fromisoformat(day)
    local_time = dt.time.fromisoformat(start_time)
    return dt.datetime.combine(day, local_time, tzinfo=LOCAL_TIMEZONE)


def plan_digest(session: dict) -> str:
    """Empreinte des champs qui rendent caduque une relance attachée à la séance."""
    visible = {key: session.get(key) for key in (
        # `calibration` n'existe plus (2026-10-09) ; sa clé reste dans l'empreinte pour
        # que les cycles déjà stockés gardent la leur et ne paraissent pas révisés.
        "title", "date", "start_time", "kind", "workout", "status", "calibration",
        "location_label", "carbs_target_g_h",
        "distance_target_km", "distance_cap_km", "duration_cap_min",
        "rpe_min", "rpe_max", "hr_zone", "cadence_target", "note",
    )}
    canonical = json.dumps(visible, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def synchronized_state(current: str | None, plan_status: str, *, changed: bool) -> str:
    """Réconcilie un cycle avec la dernière révision sans ressusciter un terminal."""
    if current == "closed":
        return current
    if current == "completed":
        return current
    if plan_status == "cancelled":
        return "cancelled"
    if plan_status == "done":
        return "completed"
    if current in TERMINAL_STATES:
        return current
    if current in OBSERVED_STATES and plan_status == "planned":
        return current
    if changed:
        return "planned"
    return current or "planned"


def local_wall_clock(value: str) -> dt.datetime:
    """Interprète l'heure locale Strava, qui ne porte volontairement aucun `Z`."""
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(LOCAL_TIMEZONE)
    return parsed.replace(tzinfo=LOCAL_TIMEZONE)
