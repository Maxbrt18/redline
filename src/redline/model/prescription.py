"""The typed prescription of a run: its relief, what its climbs and descents ask, its
structure, and the dose of its repetitions.

A planned run declares what the athlete reads before leaving and what is judged after:
the gain and loss allowed (`dplus_cap_m`, `dminus_cap_m`, an optional `dplus_target_m`),
the climb and descent intents and whether it carries prescribed efforts. Absent, a field
takes its default: easy climbs (hard on a hill session, whose climbs are the repetitions),
relaxed descents, a steady run. The write-time rules refuse a prescription that
contradicts itself before the run, such as a cap on the session's mean HR over efforts.

A session made of repetitions (`session_kinds.REPETITIONS`) may type its dose: how many
(`reps`), how long each (`rep_seconds`), the recovery between two (`recovery_seconds`)
and, off a hill, the pace of a repetition (`rep_pace_s`). The progression engine reads
this dose, and the debrief holds the repetitions run to their pace. A dose one step above
the ceiling of its lineage declares why (`override_reason`) and the prediction sealed on
it (`override_prediction`); `redline.analysis.progression_runs` checks the rest.
"""

from __future__ import annotations

from redline.coach.fr import plan as text

from . import session_kinds
from .vocabulary import (
    ClimbIntent,
    DescentIntent,
    SessionStatus,
    SessionStructure,
)

#: The relief caps apply on foot; the intents to runs only.
RELIEF_FAMILIES = frozenset({"run", "hike"})
INTENT_FIELDS = ("climb_intent", "power_hike_from_pct", "descent_intent", "structure")
RELIEF_FIELDS = ("dplus_cap_m", "dplus_target_m", "dminus_cap_m")
DOSE_FIELDS = ("reps", "rep_seconds", "recovery_seconds", "rep_pace_s")
OVERRIDE_FIELDS = ("override_reason", "override_prediction")
FIELDS = (*RELIEF_FIELDS, *INTENT_FIELDS, *DOSE_FIELDS, *OVERRIDE_FIELDS)


def climb_intent(session: dict) -> ClimbIntent:
    declared = session.get("climb_intent")
    if declared is not None:
        return ClimbIntent(declared)
    return ClimbIntent.HARD if session.get("workout") == session_kinds.HILLS else ClimbIntent.EASY


def descent_intent(session: dict) -> DescentIntent:
    declared = session.get("descent_intent")
    return DescentIntent.RELAXED if declared is None else DescentIntent(declared)


def structure(session: dict) -> SessionStructure:
    declared = session.get("structure")
    return SessionStructure.STEADY if declared is None else SessionStructure(declared)


def has_efforts(session: dict) -> bool:
    """Prescribed efforts: their pace and their HR are the prescription's, not the
    athlete's choice."""
    return (structure(session) == SessionStructure.EFFORTS
            or climb_intent(session) == ClimbIntent.HARD)


def relief_of(session: dict) -> str:
    """The relief allowed, as the athlete reads it; empty when none is declared."""
    up, target, down = (session.get("dplus_cap_m"), session.get("dplus_target_m"),
                        session.get("dminus_cap_m"))
    if up is not None and up == down and target is None:
        return text.RELIEF_BOTH_CAP.format(cap=up)
    parts = []
    if target is not None and up is not None:
        parts.append(text.DPLUS_TARGET_AND_CAP.format(target=target, cap=up))
    elif target is not None:
        parts.append(text.DPLUS_TARGET.format(target=target))
    elif up is not None:
        parts.append(text.DPLUS_CAP.format(cap=up))
    if down is not None:
        parts.append(text.DMINUS_CAP.format(cap=down))
    return ", ".join(parts)


def _duration(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    if not minutes:
        return text.SECONDS.format(seconds=rest)
    return text.MINUTES.format(minutes=minutes) + (f" {rest:02d}" if rest else "")


def dose_of(session: dict) -> str:
    """The repetitions as the athlete reads them; empty when none is typed."""
    reps, seconds = session.get("reps"), session.get("rep_seconds")
    if reps is None or seconds is None:
        return ""
    line = text.REPS.format(reps=reps, duration=_duration(seconds))
    if session.get("recovery_seconds") is not None:
        line += text.RECOVERY.format(duration=_duration(session["recovery_seconds"]))
    if session.get("rep_pace_s") is not None:
        minutes, rest = divmod(session["rep_pace_s"], 60)
        line += text.REP_PACE.format(pace=f"{minutes}:{rest:02d}")
    return line


def readout(session: dict) -> list[str]:
    """What the declared prescription asks, in reading order: repetitions, relief,
    climbs, descents, efforts. A default the coach did not declare is not shown."""
    parts = [dose_of(session), relief_of(session)]
    if session.get("climb_intent") is not None:
        parts.append(text.CLIMB_INTENTS[climb_intent(session)].format(
            grade=session.get("power_hike_from_pct")))
    if session.get("descent_intent") is not None:
        parts.append(text.DESCENT_INTENTS[descent_intent(session)])
    if session.get("structure") == SessionStructure.EFFORTS:
        parts.append(text.EFFORTS)
    return [part for part in parts if part]


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
    return problems + _dose_errors(session, where)


def _dose_errors(session: dict, where: str) -> list[str]:
    declared = [name for name in (*DOSE_FIELDS, *OVERRIDE_FIELDS)
                if session.get(name) is not None]
    if not declared:
        return []
    workout = session.get("workout")
    if workout not in session_kinds.REPETITIONS:
        return [f"{where}.{name}: only a session of repetitions types its dose"
                for name in declared]
    problems = []
    if (session.get("reps") is None) != (session.get("rep_seconds") is None):
        problems.append(f"{where}: reps and rep_seconds go together")
    if session.get("reps") is None:
        problems += [f"{where}.{name}: needs reps and rep_seconds" for name in declared
                     if name in ("recovery_seconds", "rep_pace_s", *OVERRIDE_FIELDS)]
    if (session.get("override_reason") is None) != (session.get("override_prediction") is None):
        problems.append(f"{where}: override_reason and override_prediction go together")
    if session.get("rep_pace_s") is not None and workout == session_kinds.HILLS:
        problems.append(f"{where}.rep_pace_s: a hill repetition is run by its effort; its "
                        "pace is the grade's")
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
