"""The progression engine: one method per family, declared; one engine for all of them.

The gate and the gut guard already exist as code of their own; the engine must give the
same verdicts on them, or the abstraction is wrong. The other tests hold what the engine
promises: a missing reading never passes, a rise never passes a veto, safety is never
excused, a comeback steps down.
"""

import datetime as dt
import random
from pathlib import Path

import pytest
from conftest import ZONE, zoned

from redline.analysis import ledger, progression, progression_readings, week_review
from redline.analysis.ledger import Instruments, Row
from redline.coach.fr import ledger as gate_text
from redline.model import session_kinds
from redline.model.progression import (
    Bound,
    DeclarationError,
    Disruption,
    Dose,
    Exposure,
    Lineage,
    Move,
    Reading,
    Signal,
    Verdict,
    Veto,
    VetoKind,
    default,
    parse,
)

FAMILIES = default()
MONDAY = dt.date(2026, 10, 5)


def day(offset):
    return MONDAY + dt.timedelta(days=offset)


def lineage(slug, family, parameters=(), group="", **bounds):
    return Lineage(slug, family, tuple((name, Bound(*bound)) for name, bound in bounds.items()),
                   tuple(parameters), group)


CALF = lineage("calf-raise", "strength-reps", reps=(12, 15, 1), load_kg=(6, 20, 2),
               parameters=(("difficulty_regress_above", 7), ("pain_cap", 2)))
HOLD = lineage("single-leg-stance", "strength-time", seconds=(15, 45, 5),
               parameters=(("difficulty_progress_up_to", 5), ("difficulty_regress_above", 7),
                           ("pain_cap", 2)))
HILLS = lineage("hill-threshold", "intervals", reps=(4, 6, 1), rep_seconds=(180, 300, 60),
                parameters=(("rpe_max", 7), ("reserve_min", 1), ("pain_cap", 2),
                            ("following_pain_max", 1.5)))
FUEL = lineage("long-run-carbs", "fuelling", carbs_g_h=(0, 90, 15),
               parameters=(("gut_max", 3),))


def strength(on, reps, load, counted, difficulty=6, pain=0.0, disrupted=None):
    readings = (Reading(Signal.SET_REPS_MIN, counted),
                Reading(Signal.DIFFICULTY, difficulty),
                Reading(Signal.EFFORT_PAIN, pain, ZONE))
    return Exposure(on, Dose.of(reps=reps, load_kg=load), None, readings, disrupted)


def hills(on, reps, rpe, reserve=1.0, pain=0.5, following=1.0, wake=0.0):
    readings = (Reading(Signal.SESSION_RPE, rpe), Reading(Signal.RESERVE_REPS, reserve),
                Reading(Signal.EFFORT_PAIN, pain, ZONE),
                Reading(Signal.FOLLOWING_PAIN, following, ZONE),
                Reading(Signal.WAKE_ABOVE_BASE, wake, ZONE))
    dose = Dose.of(reps=reps, rep_seconds=180)
    return Exposure(on, dose, dose, readings)


def propose(family, line, history, current=None, today=None, **kwargs):
    return progression.propose(FAMILIES[family], line, history, current=current,
                               today=today or day(40), **kwargs)


# ------------------------------------------------------------------ declaration

def test_the_engine_declares_every_family_it_ships():
    assert set(FAMILIES) == {"strength-reps", "strength-time", "strength-contacts",
                             "intervals", "fuelling", "gate"}


def test_a_declaration_reports_every_problem_at_once(tmp_path):
    text = """
[[families]]
slug = "broken"
unit = "fortnight"
ladder = []

[[families.criteria]]
id = "both"
signal = "session_rpe"
at_most = 7
at_least_ref = "rpe_min"

[[families.rule]]
id = "nothing"
move = "up"
"""
    with pytest.raises(DeclarationError) as raised:
        parse(text, Path("progression.toml"))
    message = str(raised.value)
    assert "'fortnight' unknown" in message
    assert "exactly one threshold" in message
    assert "exactly one condition" in message


# ------------------------------------------------------------------ the ladder

def test_double_progression_climbs_the_reps_then_the_load_with_the_reps_reset():
    family = FAMILIES["strength-reps"]
    assert progression.step_up(family, CALF, Dose.of(reps=13, load_kg=6)) == \
        Dose.of(reps=14, load_kg=6)
    assert progression.step_up(family, CALF, Dose.of(reps=15, load_kg=6)) == \
        Dose.of(reps=12, load_kg=8)
    assert progression.step_down(family, CALF, Dose.of(reps=12, load_kg=8)) == \
        Dose.of(reps=15, load_kg=6)
    assert progression.step_up(family, CALF, Dose.of(reps=15, load_kg=20)) is None


def test_a_free_variable_steps_back_to_the_last_lower_value_it_ran():
    family = FAMILIES["gate"]
    gate = lineage("easy-volume", "gate")
    seen = [Dose.of(easy_load=38.0), Dose.of(easy_load=41.0), Dose.of(easy_load=44.0)]
    assert progression.step_down(family, gate, Dose.of(easy_load=44.0), seen) == \
        Dose.of(easy_load=41.0)
    assert progression.step_up(family, gate, Dose.of(easy_load=44.0)) == Dose.of(easy_load=44.0)


# ------------------------------------------------------------------ strength

def test_twice_at_the_target_raises_the_dose_and_cites_the_rule():
    history = [strength(day(0), 15, 6, 15), strength(day(3), 15, 6, 15)]
    proposal = propose("strength-reps", CALF, history)
    assert proposal.move == Move.UP and proposal.clause == "twice-at-target"
    assert proposal.dose == Dose.of(reps=12, load_kg=8)
    assert proposal.ceiling == proposal.dose
    assert {item.criterion: item.verdict for item in proposal.judgements} == {
        "target-reached": Verdict.PASS, "not-too-hard": Verdict.PASS,
        "pain-under-cap": Verdict.PASS}


def test_a_set_short_of_the_target_keeps_the_dose():
    history = [strength(day(0), 15, 6, 15), strength(day(3), 15, 6, 13)]
    proposal = propose("strength-reps", CALF, history)
    assert (proposal.move, proposal.clause, proposal.dose) == (
        Move.HOLD, "keep", Dose.of(reps=15, load_kg=6))


def test_a_set_too_hard_steps_down():
    proposal = propose("strength-reps", CALF, [strength(day(0), 12, 8, 12, difficulty=9)])
    assert (proposal.move, proposal.dose) == (Move.DOWN, Dose.of(reps=15, load_kg=6))


def test_without_the_count_nothing_rises_and_the_count_is_asked():
    """The difficulty of a set stopped two reps short says nothing of the load."""
    history = [strength(day(0), 15, 6, None, difficulty=6),
               strength(day(3), 15, 6, None, difficulty=6)]
    proposal = propose("strength-reps", CALF, history, current=Dose.of(reps=15, load_kg=6))
    assert (proposal.move, proposal.clause) == (Move.HOLD, "no-evidence")
    assert proposal.missing == (Signal.SET_REPS_MIN,)
    assert proposal.unread == (day(0), day(3))


def test_a_set_too_hard_steps_down_even_without_its_count():
    proposal = propose("strength-reps", CALF, [strength(day(0), 15, 8, None, difficulty=8)])
    assert (proposal.move, proposal.clause, proposal.dose) == (
        Move.DOWN, "step-back", Dose.of(reps=14, load_kg=8))


def test_a_hold_rises_on_its_difficulty_alone():
    def held(on, difficulty):
        return Exposure(on, Dose.of(seconds=15), None,
                        (Reading(Signal.DIFFICULTY, difficulty),
                         Reading(Signal.EFFORT_PAIN, 0.0, ZONE)))

    assert propose("strength-time", HOLD, [held(day(0), 2), held(day(3), 2)]).dose == \
        Dose.of(seconds=20)
    assert propose("strength-time", HOLD, [held(day(0), 2), held(day(3), 6)]).move == Move.HOLD
    assert propose("strength-time", HOLD, [held(day(3), 8)]).move == Move.HOLD  # at the bottom
    assert propose("strength-time", HOLD, [held(day(3), 8)]).ladder_end


# ------------------------------------------------------------------ intervals

def test_a_tolerated_session_adds_a_repetition_never_pace():
    proposal = propose("intervals", HILLS, [hills(day(0), 4, 7)])
    assert proposal.move == Move.UP
    assert proposal.dose == Dose.of(reps=5, rep_seconds=180)


def test_a_reserve_not_declared_is_asked_not_assumed():
    proposal = propose("intervals", HILLS, [hills(day(0), 4, 6, reserve=None)])
    assert (proposal.move, proposal.clause) == (Move.HOLD, "wait-reading")
    assert proposal.missing == (Signal.RESERVE_REPS,)


def test_the_runs_after_a_session_must_be_in_before_it_counts():
    proposal = propose("intervals", HILLS, [hills(day(0), 4, 6, following=None)])
    assert proposal.move == Move.HOLD and Signal.FOLLOWING_PAIN in proposal.missing


def test_pain_after_a_session_steps_down():
    proposal = propose("intervals", HILLS, [hills(day(0), 5, 7, following=2.0)])
    assert (proposal.move, proposal.dose) == (Move.DOWN, Dose.of(reps=4, rep_seconds=180))


def test_a_session_rated_above_its_band_holds():
    proposal = propose("intervals", HILLS, [hills(day(0), 5, 8)])
    assert (proposal.move, proposal.clause) == (Move.HOLD, "keep")


# ------------------------------------------------------------------ vetoes and limits

@pytest.mark.parametrize("kind", [VetoKind.TAPER, VetoKind.QUALITY_HOLD, VetoKind.GATE,
                                  VetoKind.ASSIMILATION])
def test_a_rise_never_passes_a_veto(kind):
    veto = Veto(kind, "t1", "race on 2030-06-15")
    proposal = propose("intervals", HILLS, [hills(day(0), 4, 7)], vetoes=[veto])
    assert (proposal.rule_move, proposal.move) == (Move.UP, Move.HOLD)
    assert proposal.vetoes == (veto,)
    assert proposal.ceiling == Dose.of(reps=4, rep_seconds=180)


def test_a_veto_never_blocks_a_step_down():
    veto = Veto(VetoKind.TAPER, "t1", "race")
    proposal = propose("intervals", HILLS, [hills(day(0), 5, 7, pain=3.0)], vetoes=[veto])
    assert proposal.move == Move.DOWN and proposal.vetoes == ()


def test_a_veto_binds_only_the_families_that_declare_it():
    veto = Veto(VetoKind.QUALITY_HOLD, "n3", "persistent wake")
    history = [strength(day(0), 15, 6, 15), strength(day(3), 15, 6, 15)]
    assert propose("strength-reps", CALF, history, vetoes=[veto]).move == Move.UP


def test_an_outside_cause_excuses_the_effort_never_the_pain():
    heat = strength(day(3), 15, 6, 11, disrupted=Disruption.HEAT)
    history = [strength(day(0), 15, 6, 15), heat, strength(day(6), 15, 6, 15)]
    assert propose("strength-reps", CALF, history).move == Move.UP
    hurt = strength(day(3), 15, 6, 15, pain=3.0, disrupted=Disruption.HEAT)
    assert propose("strength-reps", CALF, [strength(day(0), 15, 6, 15), hurt]).move == Move.DOWN


def test_a_comeback_after_the_transition_window_steps_down():
    proposal = propose("intervals", HILLS, [hills(day(0), 6, 7)], today=day(45),
                       lookback_days=30)
    assert (proposal.move, proposal.clause, proposal.reentry) == (Move.DOWN, "reentry", True)
    assert proposal.dose == Dose.of(reps=5, rep_seconds=180)


def test_a_session_without_its_count_is_no_absence():
    """The last count is old, but the exercise was done since: no comeback to step down."""
    history = [strength(day(0), 15, 6, 15), strength(day(40), 15, 6, None)]
    proposal = propose("strength-reps", CALF, history, today=day(45), lookback_days=30)
    assert not proposal.reentry and proposal.move == Move.HOLD
    assert proposal.unread == (day(40),)


def test_a_newest_exposure_without_its_reading_waits_for_it():
    unread = Exposure(day(7), Dose.of(reps=5, rep_seconds=180), None,
                      (Reading(Signal.SESSION_RPE, None),))
    proposal = propose("intervals", HILLS, [hills(day(0), 4, 7), unread])
    assert (proposal.rule_move, proposal.move, proposal.clause) == (
        Move.UP, Move.HOLD, "await-reading")
    assert proposal.missing == (Signal.SESSION_RPE,)


def test_the_week_budget_keeps_the_rise_that_waited_longest():
    rdl = lineage("rdl", "strength-reps", reps=(8, 10, 1), load_kg=(10, 20, 2),
                  parameters=(("difficulty_regress_above", 7), ("pain_cap", 2)))
    long_wait = propose("strength-reps", CALF, [strength(day(0), 15, 6, 15),
                                                strength(day(3), 15, 6, 15),
                                                strength(day(6), 15, 6, 15)])
    short_wait = propose("strength-reps", rdl, [strength(day(3), 10, 10, 10),
                                                strength(day(6), 10, 10, 10)])
    kept, held = progression.arbitrate([short_wait, long_wait], FAMILIES, 1, "a",
                                       "one exercise of a routine at a time")[::-1]
    assert kept.move == Move.UP and kept.lineage == "calf-raise"
    assert held.move == Move.HOLD and held.vetoes[0].kind == VetoKind.BUDGET


def test_the_tally_suggests_a_bigger_step_only_after_enough_rises():
    history, reps = [], 12
    for index in range(6):
        history.append(strength(day(index * 3), reps, 6, reps))
        reps += 1 if reps < 15 else 0
    tally = progression.tally(FAMILIES["strength-reps"], CALF, history)
    assert len(tally.tolerated) == 3 and tally.suggestion is None
    longer = [strength(day(index), 12 + index, 6, 12 + index) for index in range(4)] + [
        strength(day(4), 12, 8, 12), strength(day(5), 13, 8, 13), strength(day(6), 14, 8, 14)]
    assert progression.tally(FAMILIES["strength-reps"], CALF, longer).suggestion == Move.UP


# ------------------------------------------------------------------ the gut, rewritten

def fuel(on, target, gut, taken=None):
    return progression_readings.fuelling_exposure(on, target, target if taken is None else taken,
                                                  gut)


@pytest.mark.parametrize(("baseline", "gut", "proposed", "allowed"), [
    (40, 2, 55, True), (40, 2, 56, False), (40, 3, 55, True), (40, 4, 41, False),
    (40, 4, 40, True), (40, 6, 30, True), (75, 1, 90, True), (60, 2, 80, False),
])
def test_the_gut_guard_is_one_instance_of_the_engine(baseline, gut, proposed, allowed):
    """`Plans._enforce_gut_progression`: above 3/10 no rise, otherwise 15 g/h at most."""
    proposal = propose("fuelling", FUEL, [fuel(day(0), baseline, gut)])
    assert progression.allows(FAMILIES["fuelling"], proposal,
                              Dose.of(carbs_g_h=proposed)) is allowed


def test_the_gut_step_starts_from_the_target_and_skips_unread_exposures():
    history = [fuel(day(0), 40, 2, taken=36),
               progression_readings.fuelling_exposure(day(-7), 30, None, None)]
    proposal = propose("fuelling", FUEL, history)
    assert proposal.ceiling == Dose.of(carbs_g_h=55) and proposal.unread == (day(-7),)


def test_no_fuelled_exposure_yet_bounds_nothing():
    proposal = propose("fuelling", FUEL, [], current=Dose.of(carbs_g_h=40))
    assert proposal.ceiling is None


# ------------------------------------------------------------------ the gate, rewritten

ZONES = [ledger.PainZone(ZONE, "Genou")]


def _verdict(lines):
    return lines[0].rsplit(": ", 1)[1]


def _engine_verdict(exposure):
    judgements = progression.judge(FAMILIES["gate"], GATE, exposure)
    if any(item.verdict == Verdict.FAIL for item in judgements):
        return gate_text.GATE_RED
    if any(item.verdict == Verdict.UNKNOWN for item in judgements):
        return gate_text.GATE_INCOMPLETE
    return gate_text.GATE_GREEN


GATE = lineage("easy-volume", "gate", parameters=(("gate_effort_peak", 1.5),))
VALUES = (None, 0.0, 0.5, 1.0, 1.5, 2.0)


def _random_weeks(seed):
    """Two weeks of fictional readings: calm ones, which the gate opens on, or noisy ones."""
    draw = random.Random(seed)
    calm = draw.random() < 0.5
    values = (0.0, 0.5, 1.0) if calm else VALUES
    rows = [Row(dt.date(2026, 8, 20) + dt.timedelta(days=offset), session_kinds.WAKE_TRACE,
                pains=zoned(draw.choice(VALUES[1:4]))) for offset in range(0, 38, 3)]
    peak = draw.choice(values)
    for offset in range(14):
        on = dt.date(2026, 9, 28) + dt.timedelta(days=offset)
        if calm or draw.random() < 0.6:
            wake = draw.choice((0.0, 0.5)) if calm and draw.random() < 0.9 else draw.choice(values)
            rows.append(Row(on, session_kinds.WAKE_TRACE, pains=zoned(wake)))
        if draw.random() < 0.4:
            rows.append(Row(on, "easy", km=8.0, hours=0.8 + draw.choice((0, 0.05)),
                            hr_avg=140 if calm else draw.choice((140, 141, 152)),
                            pains=zoned(peak if calm else draw.choice(values))))
    sleep = "ok" if calm and draw.random() < 0.8 else draw.choice((None, "ok", "bad_nights"))
    return rows, Instruments(sleep=sleep)


@pytest.mark.parametrize("seed", range(150))
def test_the_gate_on_the_engine_gives_the_verdict_of_the_ledger(seed):
    rows, instr = _random_weeks(seed)
    today = dt.date(2026, 10, 12)
    expected = _verdict(week_review.gate(rows, "2026-W41", instr, ZONES, today))
    exposure = progression_readings.gate_exposure(rows, "2026-W41", instr, ZONES, today)
    assert _engine_verdict(exposure) == expected


def test_the_gate_without_a_watched_zone_judges_heart_rate_and_sleep_only():
    rows = [Row(dt.date(2026, 10, 6), "easy", km=8, hours=0.8)]
    exposure = progression_readings.gate_exposure(rows, "2026-W41", Instruments(sleep="ok"),
                                                  [], dt.date(2026, 10, 12))
    assert _engine_verdict(exposure) == _verdict(
        week_review.gate(rows, "2026-W41", Instruments(sleep="ok"), [], dt.date(2026, 10, 12)))
    assert {item.criterion for item in progression.judge(FAMILIES["gate"], GATE, exposure)} == {
        "easy-hr", "sleep"}


def test_two_red_weeks_step_the_easy_load_back():
    def week(on, km, peak):
        return Exposure(on, None, Dose.of(easy_load=km),
                        (Reading(Signal.WEEK_EFFORT_PEAK, peak, ZONE),))

    history = [week(day(-14), 38.0, 1.0), week(day(-7), 42.0, 2.0)]
    one = propose("gate", GATE, history, today=day(0))
    assert (one.move, one.clause, one.ceiling) == (Move.HOLD, "red-box", Dose.of(easy_load=42.0))
    two = propose("gate", GATE, [*history, week(day(0), 42.0, 2.0)], today=day(1))
    assert (two.move, two.clause, two.ceiling) == (Move.DOWN, "two-red-weeks",
                                                   Dose.of(easy_load=38.0))
    green = propose("gate", GATE, [week(day(0), 42.0, 1.0)], today=day(1))
    assert green.move == Move.UP and green.ceiling is None


# ------------------------------------------------------------------ the readings

def test_the_readings_of_a_session_wait_for_the_morning_and_the_next_runs():
    rows = [Row(day(-10), session_kinds.WAKE_TRACE, pains=zoned(1.0)),
            Row(day(0), "threshold", km=8, hours=0.8, rpe=7, pains=zoned(1.0)),
            Row(day(1), session_kinds.WAKE_TRACE, pains=zoned(0.5)),
            Row(day(1), "easy", km=6, hours=0.6, pains=zoned(1.0)),
            Row(day(3), "easy", km=6, hours=0.6, pains=zoned(0.5))]
    dose = Dose.of(reps=4, rep_seconds=180)
    exposure = progression_readings.interval_exposure(rows, rows[1], dose, [ZONE], reserve=1)
    readings = {reading.signal: reading.value for reading in exposure.readings}
    assert readings[Signal.WAKE_ABOVE_BASE] == -0.5
    assert readings[Signal.FOLLOWING_PAIN] is None
    rows.append(Row(day(4), "long", km=16, hours=1.6, pains=zoned(1.0)))
    exposure = progression_readings.interval_exposure(rows, rows[1], dose, [ZONE], reserve=1)
    assert {reading.signal: reading.value for reading in exposure.readings}[
        Signal.FOLLOWING_PAIN] == 1.0
    assert propose("intervals", HILLS, [exposure]).dose == Dose.of(reps=5, rep_seconds=180)


def test_a_strength_report_without_counts_reads_its_difficulty_only():
    session = {"performed_on": day(0), "session_key": "w41-mar-renfo",
               "pains": {ZONE: {"before": None, "after": 0}},
               "exercises": [{"exercise_slug": "calf-raise", "difficulty": 7, "skipped": False,
                              "status": "completed", "sets": []},
                             {"exercise_slug": "jumps", "difficulty": None, "skipped": True,
                              "status": "skipped", "sets": []}]}
    exposure = progression_readings.strength_exposure([], session, "calf-raise",
                                                      Dose.of(reps=15, load_kg=6), [ZONE])
    assert exposure is not None
    readings = {reading.signal: reading.value for reading in exposure.readings}
    assert readings[Signal.SET_REPS_MIN] is None and readings[Signal.DIFFICULTY] == 7
    assert progression_readings.strength_exposure([], session, "jumps", Dose.of(contacts=40),
                                                  [ZONE]) is None
