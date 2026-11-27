"""A weekly revision held to its week in the cycle version (`redline.model.cycle_gate`)."""

from __future__ import annotations

import datetime as dt
import uuid

from redline.model import cycle_gate, cycle_templates, intensity
from redline.model import plan_generator as generator
from redline.model.cycles import CycleVersion
from redline.model.vocabulary import (
    CycleCause,
    CycleKind,
    CycleVersionState,
    GeneralLevel,
    OverrideTarget,
    PlanPhase,
    RaceTerrain,
)

TEMPLATES = cycle_templates.parse(cycle_templates.DEFAULT.read_text(encoding="utf-8"),
                                  cycle_templates.DEFAULT)
WEEK = "2030-W49"
INPUTS = generator.Inputs(WEEK, 40, 14, 50, 10, 10)
#: The first threshold at 3.5 m/s: the general key session's band, 95 to 100 % of it.
REFS = intensity.References(first_threshold_mps=3.5)
FAST, SLOW = 1000 / 3.5, 1000 / (3.5 * 0.95)


def _version(trame) -> CycleVersion:
    return CycleVersion(uuid.uuid4(), uuid.uuid4(), 1, CycleVersionState.ACTIVE, trame,
                        CycleCause.TRANSITION)


def _general_frame(**kwargs) -> cycle_gate.Frame:
    trame = generator.general(TEMPLATES.general_at(GeneralLevel.MAINTAIN), INPUTS)
    version = _version(trame)
    week = version.week(WEEK)
    return cycle_gate.Frame(version, week,
                            TEMPLATES.key_sessions(CycleKind.GENERAL, trame.template,
                                                   week.phase),
                            REFS, frozenset({"p-step"}), **kwargs)


def _session(id_, workout="easy", **fields):
    kind = "run"
    return {"id": id_, "kind": kind, "workout": workout, "status": "planned",
            "date": "2030-12-04", **fields}


def _revision(volume_km, *sessions, overrides=()):
    found = {"revision": 1, "volume_target_km": volume_km,
             "sessions": list(sessions) or [_session("easy-1", distance_target_km=8)]}
    if overrides:
        found["cycle_overrides"] = [
            {"target": target, "reason": "athlete_choice", "prediction": "p-step",
             **({"session": session} if session else {})} for target, session in overrides]
    return found


def _threshold(**fields):
    return _session("q-1", "threshold", **{"key_position": 1, "reps": 3, "rep_seconds": 480,
                                           "recovery_seconds": 60, **fields})


def test_a_week_within_its_bounds_passes_and_a_plan_may_ask_less():
    frame = _general_frame()
    assert cycle_gate.errors(frame, _revision(frame.week.volume_km, _threshold()), None) == []
    assert cycle_gate.errors(frame, _revision(10), None) == []


def test_the_general_preparation_goes_one_ramp_step_beyond_with_an_override():
    frame = _general_frame()
    above = round(frame.week.volume_km * 1.08, 1)
    [refused] = cycle_gate.errors(frame, _revision(above), None)
    assert "above the" in refused and "change the version" in refused
    assert cycle_gate.errors(frame, _revision(above, overrides=[("volume", "")]), None) == []
    beyond = round(frame.week.volume_km * 1.2, 1)
    [refused] = cycle_gate.errors(frame, _revision(beyond, overrides=[("volume", "")]), None)
    assert "an override reaches" in refused


def test_an_override_needs_an_open_prediction_and_never_comes_twice_in_a_row():
    frame = _general_frame(overridden_before=frozenset({(OverrideTarget.VOLUME, "")}))
    above = round(frame.week.volume_km * 1.05, 1)
    revision = _revision(above, overrides=[("volume", "")])
    revision["cycle_overrides"][0]["prediction"] = "p-unknown"
    found = cycle_gate.errors(frame, revision, None)
    assert any("not an open prediction" in line for line in found)
    assert any("never twice in a row" in line for line in found)


def test_an_override_nothing_goes_beyond_is_refused():
    frame = _general_frame()
    [refused] = cycle_gate.errors(frame, _revision(10, overrides=[("volume", "")]), None)
    assert "that nothing goes beyond" in refused


def test_a_preparation_s_bounds_take_no_override():
    template = TEMPLATES.preparation_for(21.1, RaceTerrain.ROAD)
    trame = generator.candidates(template, generator.Goal(dt.date(2031, 3, 30), 21.1),
                                 INPUTS)[1]
    version = _version(trame)
    week = trame.weeks[0]
    frame = cycle_gate.Frame(version, week, {}, REFS, frozenset({"p-step"}))
    revision = _revision(round(week.volume_km * 1.05, 1), overrides=[("volume", "")])
    [refused] = cycle_gate.errors(frame, revision, None)
    assert "for the general preparation only" in refused


def test_a_taper_week_is_bounded_by_the_week_before_the_taper():
    template = TEMPLATES.preparation_for(21.1, RaceTerrain.ROAD)
    trame = generator.candidates(template, generator.Goal(dt.date(2031, 3, 30), 21.1),
                                 INPUTS)[1]
    version = _version(trame)
    taper = next(week for week in trame.weeks if week.phase == PlanPhase.TAPER)
    before = trame.weeks[trame.weeks.index(taper) - 1]
    frame = cycle_gate.Frame(version, taper, {}, REFS)
    assert taper.volume_km < before.volume_km
    assert cycle_gate.errors(frame, _revision(before.volume_km), None) == [], \
        "its cut is the taper band's"
    assert cycle_gate.errors(frame, _revision(before.volume_km + 1), None)


def test_quality_sessions_keep_to_the_week_s_count_and_key_sessions():
    frame = _general_frame()
    hills = _session("q-2", "hills", reps=6, rep_seconds=60)
    found = cycle_gate.errors(frame, _revision(20, _threshold(), hills), None)
    assert any("2 quality sessions for 1" in line for line in found)
    assert any("hills is not a key session of the week (threshold)" in line for line in found)
    held = _revision(20, _threshold(), hills)
    assert cycle_gate.errors(frame, held, held) == [], "what the week already held is carried"


def test_a_session_runs_its_key_session_within_its_dose_one_step_beyond_with_an_override():
    frame = _general_frame()
    wrong = _session("q-1", "hills", key_position=1, reps=6, rep_seconds=60)
    assert any("runs the key session 1, a threshold, as a hills" in line
               for line in cycle_gate.errors(frame, _revision(20, wrong), None))
    assert "no key session at position 2" in cycle_gate.errors(
        frame, _revision(20, _session("q-1", "threshold", key_position=2)), None)[0]
    step = _threshold(rep_seconds=600)
    [refused] = cycle_gate.errors(frame, _revision(20, step), None)
    assert "1800 s of work above the key session's 1440 s" in refused
    assert cycle_gate.errors(frame, _revision(20, step, overrides=[("key_dose", "q-1")]),
                             None) == []
    two = _threshold(rep_seconds=720)
    assert cycle_gate.errors(frame, _revision(20, two, overrides=[("key_dose", "q-1")]), None)


def test_a_key_session_s_pace_stays_in_its_band_or_takes_an_override():
    frame = _general_frame()
    assert cycle_gate.errors(frame, _revision(20, _threshold(rep_pace_s=round(SLOW) - 3)),
                             None) == []
    fast = _threshold(rep_pace_s=round(FAST) - 10)
    [refused] = cycle_gate.errors(frame, _revision(20, fast), None)
    assert "outside the key session's band (286-301 s/km)" in refused
    assert cycle_gate.errors(frame, _revision(20, fast, overrides=[("rep_pace", "q-1")]),
                             None) == []
    uncalibrated = cycle_gate.Frame(frame.version, frame.week, frame.specs,
                                    intensity.References())
    assert cycle_gate.errors(uncalibrated, _revision(20, fast), None) == [], \
        "no band without the reference"
