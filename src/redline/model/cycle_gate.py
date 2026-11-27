"""A weekly revision held to its week in the cycle version: what the gate refuses.

The version that holds a week bounds it (`Frame`):
- its volume, its long run and its number of quality sessions, each quality session of a
  workout among the week's key sessions;
- a session that runs a key session (`key_position`) keeps its workout, its dose's work
  stays at or under the key session's, and its `rep_pace_s` within the pace band rendered
  from the calibration in force.

A taper week is bounded by the last week before the taper, never by its own figures: its
cut is the taper band's (`redline.analysis.taper`), which bounds the taper's mean drop and
accepts a first week that drops little.

A plan may always ask less, and what the previous revision of the week already held is
carried, not judged again. One step beyond is the bounded override (`cycle_overrides`), as
for a dose above its lineage's ceiling: a typed reason, an open prediction sealed on it,
never on the same target the week before, never declared without being used. The volume
and the long run take it in the general preparation only, at most one ramp step above the
week; a key session's dose at most its template's next step; a repetition's pace has no
step but its band. Beyond, the version is changed instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from . import intensity, session_kinds
from .cycle_templates import KeySession
from .cycles import CycleVersion
from .plan_generator import Key, Week, next_dose, work
from .progression import Dose
from .vocabulary import CycleKind, OverrideTarget, PlanPhase, SessionStatus

#: A trame's figures are rounded to 0.1 km: a bound is met within this.
EPSILON_KM = 0.05
#: Two doses of the same work, to the second.
EPSILON_S = 0.5
#: The dose variables a planned session states, as the key sessions name them.
DOSE_VARIABLES = ("reps", "rep_seconds", "recovery_seconds")


@dataclass(frozen=True)
class Frame:
    """What a week is held to: its version and week, the template's key sessions by
    workout (for their step), today's references, the open predictions and what the week
    before overrode, by target and workout."""

    version: CycleVersion
    week: Week
    specs: Mapping[str, KeySession]
    refs: intensity.References
    open_predictions: frozenset[str] = frozenset()
    overridden_before: frozenset[tuple[OverrideTarget, str]] = frozenset()

    @property
    def keys(self) -> list[Key]:
        return [key for key in self.version.trame.keys if key.week == self.week.label]

    def key_at(self, position: int) -> Key | None:
        return next((key for key in self.keys if key.position == position), None)


def _live(revision: dict | None) -> list[dict]:
    return [session for session in (revision or {}).get("sessions") or []
            if session.get("status") != SessionStatus.CANCELLED]


def _distance(session: dict) -> float | None:
    value = session.get("distance_cap_km") or session.get("distance_target_km")
    return None if value is None else float(value)


def _dose(session: dict) -> Dose | None:
    if session.get("reps") is None or session.get("rep_seconds") is None:
        return None
    return Dose(tuple((name, float(session[name])) for name in DOSE_VARIABLES
                      if session.get(name) is not None))


def _changed(session: dict, before: dict | None, names: tuple[str, ...]) -> bool:
    return before is None or any(session.get(name) != before.get(name) for name in names)


class _Overrides:
    """The overrides a revision declares, and those its checks used."""

    def __init__(self, revision: dict) -> None:
        self.declared = {(OverrideTarget(item["target"]), item.get("session") or ""): item
                         for item in revision.get("cycle_overrides") or []}
        self.used: set[tuple[OverrideTarget, str]] = set()

    def take(self, target: OverrideTarget, session: str = "") -> dict | None:
        found = self.declared.get((target, session))
        if found is not None:
            self.used.add((target, session))
        return found


def _bound(frame: Frame) -> Week:
    """The week whose volume and long run bound the revision: the last week before the
    taper for a taper week, else the week itself."""
    if frame.week.phase != PlanPhase.TAPER:
        return frame.week
    before = [week for week in frame.version.trame.weeks
              if week.phase != PlanPhase.TAPER and week.label < frame.week.label]
    return before[-1] if before else frame.week


def errors(frame: Frame, revision: dict, previous: dict | None) -> list[str]:
    """Why the revision does not hold to its week; empty when it does."""
    week, version = frame.week, frame.version
    bound = _bound(frame)
    where = f"{week.label} revision {revision.get('revision')}"
    tail = (f" -- cycle {version.kind} version {version.version} (`redline cycle show`); "
            "change the version rather than exceed its week")
    general = version.kind == CycleKind.GENERAL
    overrides = _Overrides(revision)
    live, before = _live(revision), {session["id"]: session for session in _live(previous)}
    problems: list[str] = []

    def beyond(target: OverrideTarget, value: float, bound: float, step_pct: float,
               what: str, session: str = "") -> None:
        found = overrides.take(target, session)
        step = bound * (1 + step_pct / 100)
        if found is not None and general and value <= step + EPSILON_KM:
            problems.extend(_override_errors(frame, found, target, session, where))
            return
        why = ("" if found is None else
               f" (an override reaches {step:.1f} km at most)" if general else
               " (an override is for the general preparation only)")
        problems.append(f"{where}{what}: above the {bound:g} km of the week{why}" + tail)

    inputs = version.trame.hypotheses
    volume, held = revision.get("volume_target_km"), (previous or {}).get("volume_target_km")
    if (volume is not None and volume > bound.volume_km + EPSILON_KM
            and (held is None or volume > held)):
        beyond(OverrideTarget.VOLUME, volume, bound.volume_km, inputs.ramp_max_pct,
               f".volume_target_km {volume:g} km")
    for session in live:
        if session.get("workout") != session_kinds.LONG:
            continue
        distance, old = _distance(session), before.get(session["id"])
        if (distance is not None and distance > bound.long_run_km + EPSILON_KM
                and (old is None or (_distance(old) or 0) < distance)):
            beyond(OverrideTarget.LONG_RUN, distance, bound.long_run_km,
                   inputs.long_step_max_pct, f".{session['id']}: long run of {distance:g} km")
    quality = [session for session in live if session.get("workout") in session_kinds.QUALITY]
    held_quality = [session for session in before.values()
                    if session.get("workout") in session_kinds.QUALITY]
    if len(quality) > week.qualities and len(quality) > len(held_quality):
        problems.append(f"{where}: {len(quality)} quality sessions for {week.qualities} in "
                        "the week" + tail)
    families = {key.workout for key in frame.keys}
    for session in quality:
        old = before.get(session["id"])
        if session["workout"] not in families and (
                old is None or old.get("workout") != session["workout"]):
            problems.append(f"{where}.{session['id']}: {session['workout']} is not a key "
                            f"session of the week ({', '.join(sorted(families)) or 'none'})"
                            + tail)
    for session in live:
        if session.get("key_position") is not None:
            problems += _key_errors(frame, session, before.get(session["id"]), overrides,
                                    where, tail)
    for target, named in sorted(set(overrides.declared) - overrides.used):
        problems.append(f"{where}: an override of the {target}"
                        + (f" of {named}" if named else "")
                        + " that nothing goes beyond; declare none")
    return problems


def _key_errors(frame: Frame, session: dict, before: dict | None, overrides: _Overrides,
                where: str, tail: str) -> list[str]:
    position = int(session["key_position"])
    at = f"{where}.{session['id']}"
    key = frame.key_at(position)
    if key is None:
        return [f"{at}: no key session at position {position} in the week" + tail]
    if session.get("workout") != key.workout:
        return [f"{at}: runs the key session {position}, a {key.workout}, as a "
                f"{session.get('workout')}" + tail]
    problems: list[str] = []
    dose = _dose(session)
    if (key.dose is not None and dose is not None
            and _changed(session, before, ("key_position", *DOSE_VARIABLES))
            and work(dose) > work(key.dose) + EPSILON_S):
        spec = frame.specs.get(key.workout)
        step = work(next_dose(key.dose, spec)) if spec is not None else work(key.dose)
        found = overrides.take(OverrideTarget.KEY_DOSE, session["id"])
        if found is not None and work(dose) <= step + EPSILON_S:
            problems += _override_errors(frame, found, OverrideTarget.KEY_DOSE, key.workout,
                                         at)
        else:
            problems.append(f"{at}: {work(dose):g} s of work above the key session's "
                            f"{work(key.dose):g} s" + tail)
    pace = session.get("rep_pace_s")
    band = (intensity.render(key.intensity.ref, key.intensity.band, frame.refs).pace_s
            if key.intensity is not None else None)
    if (pace is not None and band is not None
            and _changed(session, before, ("key_position", "rep_pace_s"))
            and not min(band.low, band.high) - EPSILON_S <= pace
            <= max(band.low, band.high) + EPSILON_S):
        found = overrides.take(OverrideTarget.REP_PACE, session["id"])
        if found is not None:
            problems += _override_errors(frame, found, OverrideTarget.REP_PACE, key.workout,
                                         at)
        else:
            problems.append(f"{at}: rep_pace_s {pace} outside the key session's band "
                            f"({min(band.low, band.high):.0f}-{max(band.low, band.high):.0f} "
                            "s/km)" + tail)
    return problems


def _override_errors(frame: Frame, override: dict, target: OverrideTarget, workout: str,
                     where: str) -> list[str]:
    problems = []
    if override["prediction"] not in frame.open_predictions:
        problems.append(f"{where}: the {target} override's prediction "
                        f"{override['prediction']} is not an open prediction")
    if (target, workout) in frame.overridden_before:
        problems.append(f"{where}: the {target} was already overridden the week before; "
                        "never twice in a row")
    return problems
