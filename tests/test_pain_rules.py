"""Rules n3 and n4: the part of them the engine computes and enforces."""

import datetime as dt
from dataclasses import replace

import pytest
from conftest import ZONE, zoned

from redline.analysis import ledger, pain_rules, week_review
from redline.analysis.ledger import Instruments, Row
from redline.model import iso_week, rulebook, session_kinds, weekly_plan
from redline.service import plan_revision

MONDAY = dt.date(2026, 10, 5)
#: The fixture's own scales: base light (1), cap 2.
SCALES = rulebook.current().scales
TRACE, EFFORT = SCALES.morning_trace, SCALES.effort_pain


def wakes(*values, start=MONDAY):
    """One wake-trace row per value, on consecutive mornings; None leaves a gap."""
    return [Row(start + dt.timedelta(days=offset), session_kinds.WAKE_TRACE, pains=zoned(value))
            for offset, value in enumerate(values) if value is not None]


def day(offset):
    return MONDAY + dt.timedelta(days=offset)


# ------------------------------------------------------------------ the trend

def test_the_trend_compares_the_two_halves_of_fourteen_days():
    rising = pain_rules.wake_trend(wakes(*[0] * 7, *[1] * 7), ZONE, day(13))
    assert (rising.earlier, rising.recent, rising.direction) == (0, 1, "rising")
    falling = pain_rules.wake_trend(wakes(*[1] * 7, *[0] * 7), ZONE, day(13))
    assert falling.direction == "falling"
    flat = pain_rules.wake_trend(wakes(0.5, 0, 0.5, 0, 0, 0, 0, 0.5, 0, 0), ZONE, day(13))
    assert flat.direction == "stable"


def test_a_half_without_reading_gives_no_direction():
    trend = pain_rules.wake_trend(wakes(*[None] * 7, 1, 1), ZONE, day(13))
    assert trend.direction is None and len(trend.readings) == 2


def test_the_trend_reads_one_zone_only():
    rows = [Row(day(0), session_kinds.WAKE_TRACE, pains={"heel": 2.0, ZONE: 0.0}),
            Row(day(10), session_kinds.WAKE_TRACE, pains={"heel": 0.0, ZONE: 0.0})]
    assert pain_rules.wake_trend(rows, ZONE, day(13)).direction == "stable"
    assert pain_rules.wake_trend(rows, "heel", day(13)).direction == "falling"


# ------------------------------------------------------------------ persistence

def test_three_mornings_at_the_base_or_above_establish_persistence_on_the_third():
    found = pain_rules.persistences(wakes(0, 1, 1, 2, 1, 0), ZONE, TRACE, 3)
    assert len(found) == 1
    assert found[0].established == day(3)
    assert [value for _, value in found[0].mornings] == [1, 1, 2, 1]
    assert found[0].fact() == ("observation: wake-trace knee persistent (2026-10-06 1, "
                               "2026-10-07 1, 2026-10-08 2, 2026-10-09 1) (rule n3)")


def test_an_unread_morning_or_one_below_the_base_breaks_the_run():
    assert pain_rules.persistences(wakes(1, 1, None, 1, 1), ZONE, TRACE, 3) == []
    assert pain_rules.persistences(wakes(1, 0.5, 1, 1), ZONE, TRACE, 3) == []


# ------------------------------------------------------------------ falsification

def test_settling_counts_what_followed_each_effort_above_the_cap():
    rows = [Row(day(0), "easy", pains=zoned(3.0)), *wakes(1, 0, start=day(1)),
            Row(day(4), "easy", pains=zoned(2.5)), *wakes(1, 1, start=day(5)),
            Row(day(8), "easy", pains=zoned(3.0)),
            Row(day(9), "easy", pains=zoned(2.0))]
    after = pain_rules.settling(rows, ZONE, EFFORT, TRACE)
    assert after.settled == (day(0),)
    assert after.persisted == (day(4),)
    assert after.unread == (day(8),)
    assert after.too_sensitive is None


def test_pain_that_almost_always_settles_says_the_cap_is_too_sensitive():
    rows = []
    for week in range(6):
        effort = day(7 * week)
        rows += [Row(effort, "easy", pains=zoned(3.0)), *wakes(0.5, start=effort + dt.timedelta(days=1))]
    assert pain_rules.settling(rows, ZONE, EFFORT, TRACE).too_sensitive is True
    rows += [Row(day(50), "easy", pains=zoned(3.0)), *wakes(1, 1, start=day(51))]
    assert pain_rules.settling(rows, ZONE, EFFORT, TRACE).too_sensitive is False


# ------------------------------------------------------------------ holds

def daily(*sleeps, start=MONDAY):
    return [{"checked_on": start + dt.timedelta(days=offset), "sleep": sleep}
            for offset, sleep in enumerate(sleeps)]


def test_persistence_holds_quality_for_fourteen_days():
    rows = wakes(1, 1, 1)
    [hold] = pain_rules.holds(rows, [], [ZONE], day(2))
    assert (hold.rule, hold.start, hold.end) == ("n3", day(2), day(15))
    assert pain_rules.holds(rows, [], [ZONE], day(15))
    assert pain_rules.holds(rows, [], [ZONE], day(16)) == []


def test_three_poor_nights_hold_quality_for_a_week_two_do_not():
    [hold] = pain_rules.holds([], daily("poor", "poor", "poor"), [ZONE], day(2))
    assert (hold.rule, hold.start, hold.end) == ("n4", day(2), day(8))
    assert hold.fact.startswith("observation: poor sleep on 2026-10-05")
    assert pain_rules.holds([], daily("poor", "poor", "fair", "poor"), [ZONE], day(3)) == []


def test_an_easy_run_with_a_high_hr_at_pace_holds_quality():
    runs = [Row(day(offset), "easy", km=8, hours=0.8, hr_avg=140) for offset in (0, 2, 4)]
    runs.append(Row(day(6), "easy", km=8, hours=0.8, hr_avg=150))
    [hold] = pain_rules.holds(runs, [], [ZONE], day(6))
    assert hold.rule == "n4" and hold.start == day(6)
    assert "HR 150 bpm for 140" in hold.fact


def test_without_their_thresholds_the_rules_stay_with_the_coach(monkeypatch):
    book = rulebook.current()
    bare = replace(book, parameters=replace(
        book.parameters, persistent_wake_mornings=None, alarm_no_quality_days=None))
    monkeypatch.setattr(pain_rules.rulebook, "current", lambda: bare)
    assert pain_rules.holds(wakes(1, 1, 1), daily("poor", "poor", "poor"), [ZONE],
                            day(2)) == []


def test_a_week_planned_before_persistence_is_cut_by_thirty_percent():
    before = {"revision": 1, "computed_at": "2026-10-04T18:00:00+02:00",
              "volume_target_km": 40.0}
    after = {"revision": 2, "computed_at": "2026-10-08T08:00:00+02:00",
             "volume_target_km": 28.0}
    [ceiling] = pain_rules.volume_ceilings(wakes(1, 1, 1), [ZONE], "2026-W41",
                                           [before, after], day(3))
    assert (ceiling.ceiling_km, ceiling.reference_km) == (28.0, 40.0)
    assert pain_rules.volume_ceilings(wakes(1, 1, 1), [ZONE], "2026-W41", [after], day(3)) == []


# ------------------------------------------------------------------ on write

WRITTEN = "2026-10-08T08:00:00+02:00"
HOLD = weekly_plan.QualityHold("n3", day(2), day(15), "observation: wake-trace knee persistent")


def run(identifier, date, workout, **overrides):
    payload = {"id": identifier, "title": workout, "date": date, "kind": "run",
               "workout": workout, "status": "planned", "distance_target_km": 8.0,
               "demanding": workout in session_kinds.DEMANDING, "sources": ["x"]}
    payload.update(overrides)
    return payload


def written(*sessions, volume=30.0):
    return {"revision": 2, "computed_at": WRITTEN, "volume_target_km": volume,
            "sessions": list(sessions)}


def test_a_quality_session_inside_a_hold_is_refused():
    [problem] = weekly_plan.pain_rule_errors(
        written(run("w41-thu-threshold", "2026-10-08", "threshold")), [HOLD], [])
    assert "inside the hold of rule n3" in problem
    assert "fact='observation: wake-trace knee persistent'" in problem


def test_a_quality_transition_counts_as_quality():
    easy = run("w41-sat-first", "2026-10-10", "easy", transition="quality")
    assert weekly_plan.pain_rule_errors(written(easy), [HOLD], [])


@pytest.mark.parametrize("session", [
    run("w41-thu-threshold", "2026-10-08", "threshold", status="cancelled"),
    run("w41-tue-threshold", "2026-10-06", "threshold"),
    run("w41-sat-easy", "2026-10-10", "easy"),
    run("w41-sun-long", "2026-10-11", "long"),
])
def test_what_a_hold_leaves_alone(session):
    assert weekly_plan.pain_rule_errors(written(session), [HOLD], []) == []


def test_quality_after_the_hold_is_free():
    late = run("w43-thu-threshold", "2026-10-21", "threshold")
    assert weekly_plan.pain_rule_errors(written(late), [HOLD], []) == []


def test_a_volume_above_the_ceiling_is_refused():
    ceiling = weekly_plan.VolumeCeiling("n3", "2026-W41", 28.0, 40.0, HOLD.fact)
    easy = run("w41-sat-easy", "2026-10-10", "easy")
    assert "above 28 km" in weekly_plan.pain_rule_errors(written(easy, volume=30), [], [ceiling])[0]
    assert weekly_plan.pain_rule_errors(written(easy, volume=28), [], [ceiling]) == []


class Store:
    def __init__(self, rows):
        self.rows = rows

    def daily_rows(self):
        return []

    def pain_zones(self):
        return [{"slug": ZONE, "label": "Genou", "active": True}]


def test_the_write_gate_reads_the_ledger_at_the_instant_of_writing(monkeypatch):
    store = Store(wakes(1, 1, 1))
    monkeypatch.setattr(ledger, "parse_ledger", lambda s: s.rows)
    threshold = run("w41-thu-threshold", "2026-10-08", "threshold")
    problems = plan_revision.pain_errors(store, "2026-W41", written(threshold), None,
                                         lambda message: None)
    assert len(problems) == 1 and "rule n3" in problems[0]
    later = Store(wakes(1, 1, 1, start=day(4)))
    assert plan_revision.pain_errors(later, "2026-W41", written(threshold), None,
                                     lambda message: None) == []


def test_an_unreadable_ledger_costs_the_check_not_the_prescription(monkeypatch):
    def broken(store):
        raise OSError("down")
    monkeypatch.setattr(ledger, "parse_ledger", broken)
    warned = []
    threshold = run("w41-thu-threshold", "2026-10-08", "threshold")
    assert plan_revision.pain_errors(Store([]), "2026-W41", written(threshold), None,
                                     warned.append) == []
    assert "OSError" in warned[0]


# ------------------------------------------------------------------ the gate and the rulebook

def test_a_rising_morning_trend_closes_the_gate_within_its_baseline():
    # Each morning stays within the eight-week rest peak: only the trend sees the rise.
    rows = [*wakes(1, start=day(-40)), *wakes(*[0] * 7, start=day(-7)),
            *wakes(0, 0.5, 1, 0.5, 0, 0.5, 0.5),
            Row(day(-6), "easy", km=8, hours=0.8, pains=zoned(1.0)),
            Row(day(1), "easy", km=8, hours=0.8, pains=zoned(1.0))]
    lines = week_review.gate(rows, "2026-W41", Instruments(sleep="ok"),
                        [ledger.PainZone(ZONE, "Genou")])
    assert lines[0].endswith(": rouge")
    assert any(line.startswith("  ✓ réveils : 7 chiffré(s)") for line in lines)
    assert "  ✗ réveils 14 j : 0 → 0.43 (en hausse)" in lines


def test_a_persistence_closes_the_gate():
    rows = [*wakes(*[1] * 7, start=day(-7)), *wakes(1, 1, 1, 0, 0, 0, 0)]
    lines = week_review.gate(rows, "2026-W41", Instruments(sleep="ok"),
                        [ledger.PainZone(ZONE, "Genou")])
    assert "  ✗ réveil persistant établi le 30/09 (règle n3)" in lines


def test_the_rulebook_says_what_the_code_holds_and_what_the_coach_keeps():
    rules = {rule.id: rule for rule in rulebook.current().rules}
    assert "qualité refusée 14 j" in rules["n3"].enforced_by
    assert "-30 %" in rules["n3"].enforced_by
    assert "Coach : arrêt en séance" in rules["n3"].enforced_by
    assert "3 mauvaises nuits" in rules["n4"].enforced_by
    assert "Coach : 2 nuits" in rules["n4"].enforced_by
    assert {"n3", "n4"} <= set(rulebook.ENFORCED)


def test_in_a_week_in_progress_the_gate_and_the_report_read_the_same_window():
    rows = [*wakes(*[0.5] * 7, start=day(-7)), *wakes(0, 0, 0, 0)]
    zones = [ledger.PainZone(ZONE, "Genou")]
    lines = week_review.gate(rows, "2026-W41", Instruments(sleep="ok"), zones, today=day(3))
    report = week_review.wake_lines(rows, ZONE, iso_week.as_of("2026-W41", day(3)))
    assert "  ✓ réveils 14 j : 0.5 → 0.21 (en baisse)" in lines
    assert report[0] == "  réveils 14 j : 11 lecture(s), 0.5 → 0.21 (en baisse)"
