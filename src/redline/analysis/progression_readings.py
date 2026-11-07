"""The readings of an exposure, taken from the ledger: what the progression engine judges.

Each function reads facts the engine already holds and nothing else. A reading that the
ledger does not hold yet (the morning after, the runs that follow, a count the page did
not ask for) is returned with no value: the engine treats it as unknown, never as a pass.

The gate's readings mirror `ledger.gate` box by box; `tests/test_progression.py` holds
the two to the same verdict until the gate is rendered from the engine.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from redline.model import rulebook, session_kinds
from redline.model.progression import Dose, Exposure, Reading, Signal
from redline.model.vocabulary import WeekSleep

from . import pain_rules
from .ledger import (
    Instruments,
    PainZone,
    Row,
    _week_as_of,
    _week_label,
    _week_start,
    rest_peak,
    week_volume,
)

#: The runs after a session whose effort pain says whether it was absorbed.
FOLLOWING_RUNS = 3


def _effort_peak(rows: Sequence[Row], week: str, zone: str) -> float | None:
    values = [value for row in rows if row.week == week and row.type in session_kinds.RUN
              and (value := row.pain_at(zone)) is not None]
    return max(values) if values else None


def gate_exposure(rows: Sequence[Row], label: str, instr: Instruments,
                  zones: Sequence[PainZone], today: dt.date) -> Exposure:
    """A week as the gate reads it: one reading per box and per watched zone."""
    from . import signals  # signals imports the ledger: deferred import

    previous = _week_label(_week_start(label) - dt.timedelta(days=7))
    as_of = _week_as_of(label, today)
    book = rulebook.current()
    parameters = book.parameters
    readings: list[Reading] = []
    for zone in (zone.slug for zone in zones):
        now, before = _effort_peak(rows, label, zone), _effort_peak(rows, previous, zone)
        readings.append(Reading(Signal.WEEK_EFFORT_PEAK, now, zone))
        readings.append(Reading(Signal.WEEK_PEAK_RISE,
                                None if now is None or before is None else now - before, zone))
        wakes = [row for row in rows if row.week == label and row.type == session_kinds.WAKE_TRACE]
        measured = [value for row in wakes if (value := row.pain_at(zone)) is not None]
        base = rest_peak(list(rows), _week_start(label), zone)
        above = [value for value in measured if value > (base if base is not None else 0.0)]
        complete = bool(measured) and len(measured) == len(wakes)
        readings.append(Reading(Signal.WEEK_WAKES_ABOVE_BASE,
                                len(above) if above else 0 if complete else None, zone))
        trend = pain_rules.wake_trend(list(rows), zone, as_of)
        readings.append(Reading(Signal.WEEK_WAKE_RISING,
                                None if trend.direction is None
                                else float(trend.direction == "rising"), zone))
        persistent = 0
        if parameters.persistent_wake_mornings:
            start = as_of - dt.timedelta(days=trend.days - 1)
            persistent = sum(start <= found.established <= as_of
                             for found in pain_rules.persistences(
                                 list(rows), zone, book.scales.morning_trace,
                                 parameters.persistent_wake_mornings))
        readings.append(Reading(Signal.WEEK_PERSISTENT_WAKES, persistent, zone))
    drifts = [drift for drift in signals.hr_at_pace(list(rows))
              if _week_label(drift.date) in (previous, label)
              and drift.delta >= signals.HR_DRIFT_BPM]
    readings.append(Reading(Signal.WEEK_EASY_HR_DRIFTS, len(drifts)))
    readings.append(Reading(Signal.WEEK_SLEEP_DEBT,
                            None if instr.sleep is None else float(instr.sleep != WeekSleep.OK)))
    week_rows = [row for row in rows if row.week == label]
    load = Dose.of(easy_load=round(week_volume(week_rows).km_run, 2))
    return Exposure(on=_week_start(label) + dt.timedelta(days=6), prescribed=None,
                    performed=load, readings=tuple(readings))


def after_session(rows: Sequence[Row], on: dt.date, zones: Sequence[str],
                  following: int = FOLLOWING_RUNS) -> tuple[Reading, ...]:
    """The next morning against the zone's rest baseline, and the runs that follow."""
    readings = []
    morning = on + dt.timedelta(days=1)
    for zone in zones:
        wake = next((value for row in rows if row.date == morning
                     and row.type == session_kinds.WAKE_TRACE
                     and (value := row.pain_at(zone)) is not None), None)
        base = rest_peak(list(rows), on, zone)
        readings.append(Reading(
            Signal.WAKE_ABOVE_BASE,
            None if wake is None or base is None else round(wake - base, 2), zone,
            f"wake-trace {morning.isoformat()}" if wake is not None else ""))
        runs = sorted((row for row in rows if row.date > on and row.type in session_kinds.RUN),
                      key=lambda row: row.date)[:following]
        values = [value for row in runs if (value := row.pain_at(zone)) is not None]
        readings.append(Reading(
            Signal.FOLLOWING_PAIN,
            max(values) if len(values) == following else None, zone,
            ", ".join(f"{row.type} {row.date.isoformat()}" for row in runs)))
    return tuple(readings)


def interval_exposure(rows: Sequence[Row], session: Row, dose: Dose, zones: Sequence[str],
                      reserve: float | None) -> Exposure:
    """A quality session. The reserve is declared by the athlete; absent, it is unknown."""
    fact = f"{session.type} {session.date.isoformat()}"
    readings = [Reading(Signal.SESSION_RPE, session.rpe, fact=fact),
                Reading(Signal.RESERVE_REPS, reserve, fact=fact)]
    readings.extend(Reading(Signal.EFFORT_PAIN, session.pain_at(zone), zone, fact)
                    for zone in zones)
    readings.extend(after_session(rows, session.date, zones))
    return Exposure(on=session.date, prescribed=dose, performed=dose, readings=tuple(readings))


def strength_exposure(rows: Sequence[Row], session: dict, exercise: str, dose: Dose,
                      zones: Sequence[str]) -> Exposure | None:
    """One exercise of a strength report, or None when it was skipped or held.

    The count is the fewest repetitions of a set, every side apart; sets not done count
    as none. Without any count, the reading is not taken.
    """
    result = next((item for item in session.get("exercises") or []
                   if item.get("exercise_slug") == exercise), None)
    if result is None or result.get("skipped") or result.get("status") in ("skipped", "gated"):
        return None
    on = session["performed_on"]
    fact = f"strength {session.get('session_key') or on.isoformat()} {exercise}"
    counts = [item["repetitions"] for item in result.get("sets") or []]
    partial = result.get("status") == "partial"
    readings = [
        Reading(Signal.SET_REPS_MIN, (0.0 if partial else float(min(counts))) if counts else None,
                fact=fact),
        Reading(Signal.DIFFICULTY, None if result.get("difficulty") is None
                else float(result["difficulty"]), fact=fact),
    ]
    pains = session.get("pains") or {}
    readings.extend(Reading(Signal.EFFORT_PAIN, (pains.get(zone) or {}).get("after"), zone, fact)
                    for zone in zones)
    readings.extend(reading for reading in after_session(rows, on, zones)
                    if reading.signal == Signal.WAKE_ABOVE_BASE)
    return Exposure(on=on, prescribed=dose, performed=None, readings=tuple(readings))


def fuelling_exposure(on: dt.date, target_g_h: float, carbs_taken: float | None,
                      gut_symptoms: float | None, fact: str = "") -> Exposure:
    """A fuelled session: the target prescribed, what was taken, the gut's answer."""
    return Exposure(on=on, prescribed=Dose.of(carbs_g_h=target_g_h),
                    performed=None if carbs_taken is None else Dose.of(carbs_g_h=carbs_taken),
                    readings=(Reading(Signal.GUT_SYMPTOMS, gut_symptoms, fact=fact),
                              Reading(Signal.CARBS_TAKEN, carbs_taken, fact=fact)))
