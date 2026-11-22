"""The typed prescription of a run: its relief, what its climbs and descents ask, its
structure.

A planned run declares what the athlete reads before leaving and what is judged after:
the gain and loss allowed (`dplus_cap_m`, `dminus_cap_m`, an optional `dplus_target_m`),
the climb and descent intents and whether it carries prescribed efforts. Absent, a field
takes its default: easy climbs (hard on a hill session, whose climbs are the repetitions),
relaxed descents, a steady run. The write-time rules refuse a prescription that
contradicts itself before the run, such as a cap on the session's mean HR over efforts.
"""

from __future__ import annotations

from . import session_kinds
from .vocabulary import ClimbIntent, SessionStatus, SessionStructure

#: The relief caps apply on foot; the intents to runs only.
RELIEF_FAMILIES = frozenset({"run", "hike"})
INTENT_FIELDS = ("climb_intent", "power_hike_from_pct", "descent_intent", "structure")
RELIEF_FIELDS = ("dplus_cap_m", "dplus_target_m", "dminus_cap_m")
FIELDS = (*RELIEF_FIELDS, *INTENT_FIELDS)


def climb_intent(session: dict) -> ClimbIntent:
    declared = session.get("climb_intent")
    if declared is not None:
        return ClimbIntent(declared)
    return ClimbIntent.HARD if session.get("workout") == session_kinds.HILLS else ClimbIntent.EASY


def structure(session: dict) -> SessionStructure:
    declared = session.get("structure")
    return SessionStructure.STEADY if declared is None else SessionStructure(declared)


def has_efforts(session: dict) -> bool:
    """Prescribed efforts: their pace and their HR are the prescription's, not the
    athlete's choice."""
    return (structure(session) == SessionStructure.EFFORTS
            or climb_intent(session) == ClimbIntent.HARD)


def _mean_hr_cap_refused(session: dict) -> bool:
    return session.get("hr_cap_bpm") is not None and has_efforts(session)


def session_errors(session: dict, where: str) -> list[str]:
    """What a single planned session's typed prescription contradicts."""
    if session.get("status") == SessionStatus.CANCELLED:
        return []
    problems = []
    workout = session.get("workout")
    family = session_kinds.family(workout) if workout in session_kinds.BY_SLUG else None
    if family not in RELIEF_FAMILIES:
        problems += [f"{where}.{name}: only a run or a hike carries relief"
                     for name in RELIEF_FIELDS if session.get(name) is not None]
    if family != "run":
        problems += [f"{where}.{name}: only a run carries a climb or descent intent or a "
                     "structure" for name in INTENT_FIELDS if session.get(name) is not None]
        return problems
    target, cap = session.get("dplus_target_m"), session.get("dplus_cap_m")
    if target is not None and cap is not None and target > cap:
        problems.append(f"{where}: D+ target {target} m above the cap {cap} m")
    declared = session.get("climb_intent")
    if declared is not None and (declared == ClimbIntent.HARD) != (workout == session_kinds.HILLS):
        problems.append(f"{where}.climb_intent: hard climbs belong to a hill session, and a "
                        "hill session's climbs are hard")
    if (climb_intent(session) == ClimbIntent.POWER_HIKE) != (
            session.get("power_hike_from_pct") is not None):
        problems.append(f"{where}.power_hike_from_pct: the grade brisk walking starts at "
                        "goes with climb_intent=power_hike, and only with it")
    if session.get("structure") is not None and workout not in session_kinds.EASY_RUNS:
        problems.append(f"{where}.structure: only an easy or long run declares a structure; "
                        "a quality session's structure is its workout")
    return problems


def errors(previous: dict | None, current: dict, where: str = "revision") -> list[str]:
    """The typed prescription of a new revision. A mean HR cap over efforts the previous
    revision already held on the same session is carried, not given: refusing it would
    block every revision of a week planned before the rule."""
    before = {session["id"]: session for session in (previous or {}).get("sessions") or []}
    problems = []
    for session in current.get("sessions") or []:
        label = f"{where}.{session.get('id', '?')}"
        problems += session_errors(session, label)
        held = before.get(session.get("id"))
        if (session.get("status") != SessionStatus.CANCELLED and _mean_hr_cap_refused(session)
                and not (held is not None and _mean_hr_cap_refused(held)
                         and held.get("hr_cap_bpm") == session.get("hr_cap_bpm"))):
            problems.append(f"{label}.hr_cap_bpm: no cap on the session's mean HR over "
                            "prescribed efforts -- the efforts raise it by design; leave "
                            "hr_zone and hr_cap_bpm out")
    return problems
