"""Pure state machine of a session, independent of transport and storage.

The program says what must happen. This module says where that session stands in its
operational cycle. Reminders and debriefs hook onto these states; they do not each
redefine them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from .local_time import zone as local_time_zone
from .vocabulary import CycleState, SessionStatus

TERMINAL_STATES = frozenset({CycleState.MISSED, CycleState.CANCELLED, CycleState.CLOSED})
# A cycle whose execution has already left a trace never reverts to a mere intention
# because the program is revised afterwards.
OBSERVED_STATES = frozenset({CycleState.STARTED, CycleState.COMPLETED})
#: The states after which nothing about the session is still awaited.
SETTLED_STATES = TERMINAL_STATES | {CycleState.COMPLETED}


def scheduled_for(day: str | dt.date, start_time: str | None) -> dt.datetime | None:
    """Combine the plan date and its local time into an unambiguous instant."""
    if not start_time:
        return None
    if isinstance(day, str):
        day = dt.date.fromisoformat(day)
    local_time = dt.time.fromisoformat(start_time)
    return dt.datetime.combine(day, local_time, tzinfo=local_time_zone())


def plan_digest(session: dict) -> str:
    """Fingerprint of the fields that make a reminder attached to the session stale."""
    visible = {key: session.get(key) for key in (
        # No session carries `calibration`; its key stays in the fingerprint so that
        # stored cycles keep theirs and do not appear revised.
        "title", "date", "start_time", "kind", "workout", "routine", "status", "calibration",
        "location_label", "carbs_target_g_h",
        "distance_target_km", "distance_cap_km", "duration_cap_min",
        "rpe_min", "rpe_max", "hr_zone", "cadence_target", "note",
    )}
    # Added only when present, so the cycles stored before the field keep their print.
    if session.get("carbs_mix") is not None:
        visible["carbs_mix"] = session["carbs_mix"]
    canonical = json.dumps(visible, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def synchronized_state(current: str | None, plan_status: str, *, changed: bool) -> str:
    """Reconcile a cycle with the latest revision without resurrecting a terminal state."""
    if current in {CycleState.CLOSED, CycleState.COMPLETED}:
        return current
    if plan_status == SessionStatus.CANCELLED:
        return CycleState.CANCELLED
    if plan_status == SessionStatus.DONE:
        return CycleState.COMPLETED
    if current in TERMINAL_STATES:
        return current
    if current in OBSERVED_STATES and plan_status == SessionStatus.PLANNED:
        return current
    if changed:
        return CycleState.PLANNED
    return current or CycleState.PLANNED


def local_wall_clock(value: str) -> dt.datetime:
    """Interpret Strava local time, which deliberately carries no `Z`."""
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(local_time_zone())
    return parsed.replace(tzinfo=local_time_zone())
