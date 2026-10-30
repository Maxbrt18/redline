"""Pure state machine of a session, independent of transport and storage.

The program says what must happen. This module says where that session stands in its
operational cycle. Reminders and debriefs hook onto these states; they do not each
redefine them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from redline.model.vocabulary import MORNING_TRACE_PAIN, MorningTrace

from .local_time import zone as local_time_zone

TERMINAL_STATES = frozenset({"missed", "cancelled", "closed"})
# A cycle whose execution has already left a trace never reverts to a mere intention
# because the program is revised afterwards.
OBSERVED_STATES = frozenset({"started", "completed"})
#: "Very light" (0.5) is distinct: it is neither light nor none.
MORNING_TRACES = tuple(MorningTrace)
MORNING_TRACE_VALUES = MORNING_TRACE_PAIN


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
        # `calibration` no longer exists; its key stays in the fingerprint so that
        # stored cycles keep theirs and do not appear revised.
        "title", "date", "start_time", "kind", "workout", "routine", "status", "calibration",
        "location_label", "carbs_target_g_h",
        "distance_target_km", "distance_cap_km", "duration_cap_min",
        "rpe_min", "rpe_max", "hr_zone", "cadence_target", "note",
    )}
    canonical = json.dumps(visible, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def synchronized_state(current: str | None, plan_status: str, *, changed: bool) -> str:
    """Reconcile a cycle with the latest revision without resurrecting a terminal state."""
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
    """Interpret Strava local time, which deliberately carries no `Z`."""
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(local_time_zone())
    return parsed.replace(tzinfo=local_time_zone())
