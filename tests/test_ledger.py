"""Unit tests for scripts/ledger.py -- the load gauge and the trends.

The calibration week (16-22 Feb 2026 = 100 by definition, coaching/protocols.md) is the
non-regression test of the whole gauge: if a change breaks it, the gauge stops meaning
100 = the week that broke the athlete, and every reading downstream lies.
"""

import datetime as dt

import pytest
from conftest import RPE_TARGETS, VAULT_ROOT, ZONE, zoned

from redline.analysis import ledger
from redline.analysis.ledger import Instruments, Row
from redline.model import ledger as table
from redline.model import programme, session_kinds
from redline.model.notes import NoteError


def _etalon_rows():
    """A reference week: 47.5 km / ~4.40 h of running over four sessions incl. two
    intensity sessions, ~150 m D+, on three consecutive demanding days, poor sleep."""
    return [
        # D- = D+: urban loops give back what they climb. Explicit hypothesis now that the
        # elevation term reads D-: it leaves the reference week at 100, which this test checks.
        Row(dt.date(2026, 2, 17), "threshold", km=11.5, hours=1.10, dplus=50, dminus=50),
        Row(dt.date(2026, 2, 18), "long", km=15.6, hours=1.40, dplus=50, dminus=50),
        Row(dt.date(2026, 2, 19), "threshold", km=15.9, hours=1.50, dplus=50, dminus=50),
        Row(dt.date(2026, 2, 21), "race", km=4.6, hours=0.40),
    ]


def test_calibration_week_is_100():
    h_eq, level, _ = ledger.equivalent_hours(
        _etalon_rows(), Instruments(sleep="bad_nights"), RPE_TARGETS)
    assert abs(h_eq - 7.15) < 0.1, h_eq
    assert abs(level - 100) <= 2, level


def test_gauge_prints_its_inputs():
    _, _, detail = ledger.equivalent_hours(_etalon_rows(),
                                           Instruments(sleep="bad_nights"),
                                           RPE_TARGETS)
    assert any("×1.15" in d for d in detail)     # three consecutive demanding days
    assert any("×1.10" in d for d in detail)     # poor sleep
    assert any("h-éq" in d for d in detail)


def test_rated_session_adds_half_an_hour():
    """Garde-fou n°6: a séance run above its RPE target costs +0.5 h-éq."""
    on_target = [Row(dt.date(2026, 3, 2), "threshold", km=8, hours=0.75, rpe=6.0)]
    over = [Row(dt.date(2026, 3, 2), "threshold", km=8, hours=0.75, rpe=8.0)]
    assert ledger.equivalent_hours(over, Instruments(), RPE_TARGETS)[0] == \
        ledger.equivalent_hours(on_target, Instruments(), RPE_TARGETS)[0] + 0.5


# ------------------------------ the elevation term, and who owns it

def test_a_bike_carries_no_elevation_term():
    """`protocols.md` gives the elevation term to running (full) and hiking (half). A
    bike ("Z2: x0.5") carries none -- the code wrote `else 1.0`, which gave a bike the
    FULL weight of a run.

    The underlying reason: what the term approximates is the MECHANICAL load of a
    descent -- impact, braking, eccentric work ([[src-vernillo-course-en-pente]]). A bike
    has neither. The ENERGY cost of elevation is comparable between the two, but it is
    not what the formula bills ([[src-minetti-cout-pente]]).
    """
    velo = Row(dt.date(2026, 8, 13), "bike", hours=1.716, dplus=647.5, dminus=648)
    assert ledger.row_weight(velo, RPE_TARGETS)[0] == pytest.approx(0.858, abs=0.01)


def test_a_hilly_run_keeps_its_full_elevation_term():
    """The negative control: removing the term on a bike removes it from nothing else.
    On a loop (D- = D+), the change of reading moves nothing -- that is the point."""
    course = Row(dt.date(2026, 8, 16), "long", km=10.3, hours=1.152, dplus=600, dminus=600)
    rando = Row(dt.date(2026, 7, 21), "hike", hours=4.0, dplus=600, dminus=600)
    assert ledger.row_weight(course, RPE_TARGETS)[0] == pytest.approx(2.152, abs=0.01)
    assert ledger.row_weight(rando, RPE_TARGETS)[0] == pytest.approx(2.5, abs=0.01)


def test_only_running_and_walking_carry_a_descent_weight():
    """A descent counts in full on foot, half walked, and not at all on a bike."""
    weights = {kind: session_kinds.descent_weight(kind) for kind in table.SESSION_TYPES}
    assert weights["long"] == 1.0 and weights["hike"] == 0.5
    assert weights["bike"] == weights["strength"] == 0.0


# ------------------------------------------------- the RPE target, single source

def test_the_targets_come_from_the_workout_notes():
    """`scripts/ledger.py` used to carry a hard-coded COPY of the `rpe_cible`: dead
    entries and missing notes meant a strength session above its target triggered
    nothing. The numbers now exist once. This is the only test in the suite that really
    reads the notes: it tells that the literal table in conftest has not fallen behind."""
    assert programme.rpe_targets(VAULT_ROOT) == RPE_TARGETS


def test_a_workout_note_declaring_an_unknown_kind_is_refused(tmp_path):
    """A note's `kinds:` must name registry kinds, or its RPE target would match nothing."""
    workouts = tmp_path / "coaching" / "workouts"
    workouts.mkdir(parents=True)
    (workouts / "ef.md").write_text("---\ntype: seance-type\nkinds: [EF]\nrpe_cible: 3\n---\n",
                                    encoding="utf-8")
    with pytest.raises(NoteError, match=r"ef\.md"):
        programme.rpe_targets(tmp_path)


def test_a_session_above_its_target_is_flagged_whatever_its_type():
    """The case that triggered it: a strength session at RPE 7 for a target of 6."""
    rows = [Row(dt.date(2026, 8, 6), "strength", routine="b", hours=0.73, rpe=7.0),
            Row(dt.date(2026, 7, 31), "strength", routine="b", hours=0.42, rpe=3.0)]
    flags = ledger.over_target(rows, RPE_TARGETS)
    assert [f.date for f in flags] == [dt.date(2026, 8, 6)]
    assert "RPE 7 déclaré pour une cible de 6" in str(flags[0])


def test_the_gauge_penalty_stays_restricted_to_quality_sessions():
    """Flagging is not penalising. The +0.5 "failed session" was calibrated on quality
    sessions only; extending it to strength would rescale the gauge, which is a coaching
    decision and not a side effect of this fix."""
    over = [Row(dt.date(2026, 8, 6), "strength", routine="b", hours=1.0, rpe=9.0)]
    on_target = [Row(dt.date(2026, 8, 6), "strength", routine="b", hours=1.0, rpe=6.0)]
    assert ledger.equivalent_hours(over, Instruments(), RPE_TARGETS)[0] == \
        ledger.equivalent_hours(on_target, Instruments(), RPE_TARGETS)[0]
    assert ledger.over_target(over, RPE_TARGETS)      # but the flag does go out


def test_rando_is_half_weighted():
    rando = [Row(dt.date(2026, 3, 2), "hike", km=20, hours=4.0, dplus=600, dminus=600)]
    h_eq, _, _ = ledger.equivalent_hours(rando, Instruments(), RPE_TARGETS)
    assert h_eq == 4.0 * 0.5 + (600 / 600) * 0.5     # 2.0 + 0.5 = 2.5


def test_a_typed_strength_report_enters_the_gauge_without_a_second_observation():
    class Store:
        def observation_rows(self):
            return []

        def strength_history(self):
            return [{
                "performed_on": dt.date(2026, 8, 26), "workout": "a",
                "duration_minutes": 60, "pains": {ZONE: {"before": None, "after": 1}},
                "note": "Planche unipodale trop dure.",
            }]

    rows = ledger.parse_ledger(Store())

    assert rows == [Row(
        dt.date(2026, 8, 26), "strength", routine="a", hours=1.0, pains=zoned(1.0),
        note="Planche unipodale trop dure.")]
    assert ledger.row_weight(rows[0], RPE_TARGETS)[0] == 0.5


def test_an_old_strength_observation_wins_instead_of_being_counted_twice():
    class Store:
        def observation_rows(self):
            return [{
                "occurred_on": dt.date(2026, 8, 18), "kind": "strength", "routine": "a",
                "measurements": {
                    "duration_s": {"state": "known", "value": 3540},
                    "rpe": {"state": "known", "value": 7},
                },
                "pains": {ZONE: {"state": "known", "value": 1}},
                "note": "Observation historique enrichie.",
            }]

        def strength_history(self):
            return [{
                "performed_on": dt.date(2026, 8, 18), "workout": "a",
                "duration_minutes": 59, "pains": {ZONE: {"before": None, "after": 1}},
                "note": "Rapport page.",
            }]

    rows = ledger.parse_ledger(Store())

    assert len(rows) == 1
    assert rows[0].rpe == 7
    assert rows[0].note == "Observation historique enrichie."


def test_consecutive_hard_days_detected():
    rows = [Row(dt.date(2026, 2, 20), "long", hours=6.0),
            Row(dt.date(2026, 2, 21), "hike", hours=2.5)]
    assert ledger._two_hard_days_in_a_row(rows)
    spaced = [Row(dt.date(2026, 2, 20), "long", hours=6.0),
              Row(dt.date(2026, 2, 23), "hike", hours=2.5)]
    assert not ledger._two_hard_days_in_a_row(spaced)


def test_ratio_over_four_previous_weeks():
    per_week = {"2026-W10": 2.0, "2026-W11": 2.0, "2026-W12": 2.0,
                "2026-W13": 2.0, "2026-W14": 4.0}
    assert ledger.acute_chronic_ratio(per_week, "2026-W14") == 2.0


def test_ratio_counts_an_empty_week_as_zero():
    """A week with no session is a real 0 in the chronic load, not a week to skip.
    Skipping it inflates the denominator and hides the alarm -- and that alarm is the
    relapse: a jump right after an almost empty week."""
    with_gap = {"2026-W10": 2.0, "2026-W11": 2.0, "2026-W12": 2.0,
                "2026-W14": 4.0}                      # W13 = total rest
    # chronic = (2 + 2 + 0 + 2) / 4 = 1.5  ->  4 / 1.5 = 2.67, not 2.0
    assert ledger.acute_chronic_ratio(with_gap, "2026-W14") == 2.67


def test_ratio_ignores_weeks_before_the_ledger_starts():
    """No data is not a zero: the first weeks must not be padded with rest."""
    assert ledger.acute_chronic_ratio({"2026-W10": 2.0, "2026-W11": 4.0},
                                       "2026-W11") == 2.0


# --------------------------------------------------------------------------- pain

def test_pain_trend_reports_the_weekly_peak():
    """A median over a week of zero-pain hikes is 0 by construction: it reported W30 at
    0.0 while the two most recent readings were 1.5 and 1-1.5."""
    rows = [Row(dt.date(2026, 7, 20), "hike", pains=zoned(0.0)),
            Row(dt.date(2026, 7, 21), "hike", pains=zoned(0.0)),
            Row(dt.date(2026, 7, 22), "rest-reading", pains=zoned(1.5)),
            Row(dt.date(2026, 7, 23), "easy", pains=zoned(1.5))]
    week = ledger.pain_trend(rows, 1, ZONE)[0]
    assert week.effort.peak == 1.5
    assert week.effort.median == 0.0        # three sessions: 0, 0, 1.5
    assert week.effort.n == 3


def test_pain_during_effort_and_at_rest_are_two_series():
    """Pooled, then reduced to a weekly max, the two answer neither question: a 1.5 felt
    in the climbs and a 0.5 read cold that evening would come out as a single "pic 1.5"
    -- the same number an untouched injury site at rest would give."""
    rows = [Row(dt.date(2026, 7, 29), "hills", pains=zoned(1.5)),
            Row(dt.date(2026, 7, 29), "rest-reading", pains=zoned(0.5)),
            Row(dt.date(2026, 7, 30), "wake-trace", pains=zoned(0.5))]
    week = ledger.pain_trend(rows, 1, ZONE)[0]
    assert week.effort.peak == 1.5 and week.effort.n == 1
    assert week.rest.peak == 0.5 and week.rest.n == 2      # reading + wake
    assert week.wake.peak == 0.5 and week.wake.n == 1      # the wake alone


def test_a_peak_is_printed_with_its_count():
    """A max over three readings and a max over seven are not comparable: without n,
    the more diligently the athlete declares, the worse the trend looks."""
    week = ledger.pain_trend([Row(dt.date(2026, 7, 29), "easy", pains=zoned(1.0))], 1, ZONE)[0]
    assert "n=1" in str(week)


def test_a_rest_reading_never_enters_the_gauge():
    for kind in ("rest-reading", "wake-trace"):
        row = Row(dt.date(2026, 7, 29), kind, hours=0.5, pains=zoned(1.0))
        assert ledger.row_weight(row, RPE_TARGETS) == (0.0, False)


def test_a_palpation_enters_neither_pain_series():
    """A pain PROVOKED by pressure is neither an effort peak nor a spontaneous reading at
    rest: it is a localisation. The series used to be defined by negation
    (`not in REST_TYPES`), so a new type fell silently into the effort series, the one
    that judges the injury."""
    week = ledger.pain_trend([Row(dt.date(2026, 7, 29), "easy", pains=zoned(1.0)),
                              Row(dt.date(2026, 7, 30), "palpation", pains=zoned(4.0))],
                             1, ZONE)[0]
    assert week.effort.peak == 1.0
    assert week.rest.peak is None


def test_a_palpation_is_not_a_session_the_esculier_rule_can_judge():
    rows = [Row(dt.date(2026, 7, 30), "palpation", pains=zoned(4.0))]
    assert ledger.esculier_check(rows, ZONE) == []


def test_a_palpation_requires_no_cell():
    """Written as `lecture`, a palpation would demand a `Dlr` on a scale that does not
    exist for it."""
    assert table.required_columns("palpation") == ()
    assert not table.requires_pain("palpation")
    assert table.requires_pain("rest-reading") and table.required_columns("rest-reading") == ()


# ---------------------------------------------------------------------- Esculier

def test_a_morning_above_its_own_rest_series_is_flagged():
    """The morning is judged against the rest series, and here there is none to speak of:
    a 2.0 with no history behind it is named once."""
    rows = [Row(dt.date(2026, 7, 30), "easy", pains=zoned(1.0)),
            Row(dt.date(2026, 7, 31), "wake-trace", pains=zoned(2.0))]
    flags = ledger.esculier_check(rows, ZONE)
    assert [f.kind for f in flags] == ["TRACE AU RÉVEIL"]
    assert "2.0/10 au réveil" in flags[0].detail


def test_a_morning_inside_its_own_range_no_longer_fires_at_all():
    """A morning at 2.0 inside its own rest range is not news: the flag is fixed at the
    trigger instead of in the message.

    A `reveil` at 2.0 was read as a change of category while the rest series had sat
    between 0.5 and 2.0 for five weeks. Printing the series next to the flag was not
    enough; the flag still fired, because it compared the morning to the previous day's
    EFFORT peak -- two currencies, one ruler. The /10 scale is written for effort only,
    so that subtraction is gone: 2.0 equals the peak of its own eight weeks, therefore
    it is not news, therefore silence.
    """
    rows = [Row(dt.date(2026, 7, 9), "rest-reading", pains=zoned(2.0)),
            Row(dt.date(2026, 7, 22), "rest-reading", pains=zoned(1.5)),
            Row(dt.date(2026, 7, 29), "rest-reading", pains=zoned(0.5)),
            Row(dt.date(2026, 8, 10), "easy", pains=zoned(1.0)),
            Row(dt.date(2026, 8, 11), "wake-trace", pains=zoned(2.0))]
    assert ledger.esculier_check(rows, ZONE) == []


def test_a_morning_that_leaves_its_own_range_still_fires_with_its_series():
    """Above the rest peak, it is a signal -- and it goes out with the series that frames
    it, so the reader can see how far out it actually is."""
    rows = [Row(dt.date(2026, 7, 9), "rest-reading", pains=zoned(2.0)),
            Row(dt.date(2026, 7, 22), "rest-reading", pains=zoned(1.5)),
            Row(dt.date(2026, 8, 10), "easy", pains=zoned(1.0)),
            Row(dt.date(2026, 8, 11), "wake-trace", pains=zoned(2.5))]
    flags = ledger.esculier_check(rows, ZONE)
    assert [f.kind for f in flags] == ["TRACE AU RÉVEIL"]
    assert "pic 2.0" in flags[0].detail
    assert "médiane 1.75" in flags[0].detail


def test_a_calm_morning_after_a_painful_session_is_not_a_violation():
    """The session hurt more than the morning: that is the rule being PASSED, and it used
    to be the only way to stay silent."""
    rows = [Row(dt.date(2026, 8, 10), "easy", pains=zoned(2.0)),
            Row(dt.date(2026, 8, 11), "wake-trace", pains=zoned(0.0))]
    assert ledger.esculier_check(rows, ZONE) == []


def test_the_baseline_only_looks_backwards():
    """A reading cannot be its own reference: the morning being judged, and everything
    after it, stay out of the window."""
    rows = [Row(dt.date(2026, 8, 10), "easy", pains=zoned(1.0)),
            Row(dt.date(2026, 8, 11), "wake-trace", pains=zoned(2.0)),
            Row(dt.date(2026, 8, 12), "rest-reading", pains=zoned(4.0))]
    detail = ledger.esculier_check(rows, ZONE)[0].detail
    assert "aucun historique de repos" in detail


def test_the_baseline_window_forgets_old_readings():
    """Eight weeks: a rest series from last spring says nothing about this block."""
    rows = [Row(dt.date(2026, 5, 1), "rest-reading", pains=zoned(3.0)),
            Row(dt.date(2026, 8, 10), "easy", pains=zoned(1.0)),
            Row(dt.date(2026, 8, 11), "wake-trace", pains=zoned(2.0))]
    assert "aucun historique de repos" in ledger.esculier_check(rows, ZONE)[0].detail


def test_a_session_with_pain_and_no_morning_reading_is_flagged():
    """An untested rule is not a passed rule."""
    rows = [Row(dt.date(2026, 7, 30), "hills", pains=zoned(1.5))]
    assert [f.kind for f in ledger.esculier_check(rows, ZONE)] == ["non vérifié"]


def test_the_missing_reading_flag_does_not_judge_the_past():
    """Before `reveil` existed, no morning reading could have been taken. Flagging
    those rows would print six lines nobody can act on, every single run."""
    rows = [Row(dt.date(2026, 7, 10), "easy", pains=zoned(1.5))]
    assert ledger.esculier_check(rows, ZONE) == []


def test_pain_above_the_cap_is_flagged_whatever_the_next_day_says():
    rows = [Row(dt.date(2026, 7, 30), "easy", pains=zoned(3.0)),
            Row(dt.date(2026, 7, 31), "wake-trace", pains=zoned(0.0))]
    assert [f.kind for f in ledger.esculier_check(rows, ZONE)] == ["AU-DESSUS DU PLAFOND"]


def test_the_descent_of_konigssee_no_longer_weighs_zero():
    """The descent term reads D-: a pure descent no longer weighs zero.

    A descent with 1096 m of D- for 20 m of D+ used to count only the D+, so the most
    mechanically loaded day weighed zero elevation, while a pure climb right after it
    (1108 m of D+, 17 m of D-) was over-billed.

    What hours do not capture is the descent: it is faster, so it costs little time,
    while it costs the most in mechanics.
    """
    rows = [Row(dt.date(2026, 7, 21), "hike", hours=2.5, dplus=20, dminus=1096)]
    assert ledger.week_descent(rows) == 1096
    assert ledger.equivalent_hours(rows, Instruments(), RPE_TARGETS)[0] == \
        pytest.approx(1.25 + (1096 / 600) * 0.5, abs=0.01)


def test_a_pure_climb_is_no_longer_billed_as_a_descent():
    """The symmetric control: a climb (1108 m D+, 17 m D-) is paid by the time it takes,
    not by an elevation term that counts it twice."""
    rows = [Row(dt.date(2026, 7, 19), "hike", hours=4.0, dplus=1108, dminus=17)]
    assert ledger.equivalent_hours(rows, Instruments(), RPE_TARGETS)[0] == \
        pytest.approx(2.0 + (17 / 600) * 0.5, abs=0.01)


# ------------------------------------------------------------------------ the volume

def _w31_rows():
    """W31 as it really happened -- the week announced as "22 km"."""
    return [
        Row(dt.date(2026, 7, 28), "easy", km=7.19, hours=0.756, dplus=44, dminus=41),
        Row(dt.date(2026, 7, 29), "hills", km=8.32, hours=0.805, dplus=175, dminus=176),
        Row(dt.date(2026, 7, 31), "strength", routine="b", hours=0.416),
        Row(dt.date(2026, 8, 1), "easy", km=9.00, hours=0.969, dplus=87, dminus=87),
    ]


def test_week_volume_is_the_sum_of_the_ledger_not_the_plan():
    """A "22 km" announced for a 24.51 km week: the 22 was the `Vol. cible`
    of current-plan.md, quoted as a fact. The gauge has to be able to print the real
    number, or the only figure at hand stays the planned one."""
    volume = ledger.week_volume(_w31_rows())
    assert volume.km_run == 24.51
    assert volume.runs == 3                      # strength carries no km
    assert "24.51 km courus (3 sorties)" in str(volume)


def test_rounding_does_not_accumulate_over_a_week():
    """Tested against Strava's own metres: 8995.5 + 8316.3 +
    7189.9 = 24501.7 m. The ledger's two decimals cost 8 m ON THE WEEK, and the error
    is bounded per session, not cumulative -- a 2.5 km gap is never rounding."""
    strava_m = [8995.5, 8316.3, 7189.9]
    assert abs(ledger.week_volume(_w31_rows()).km_run - sum(strava_m) / 1000) < 0.01


def test_walking_km_are_counted_apart_from_running_km():
    rows = [Row(dt.date(2026, 7, 20), "hike", km=12.0, hours=4.0),
            Row(dt.date(2026, 7, 22), "easy", km=6.0, hours=0.6)]
    volume = ledger.week_volume(rows)
    assert (volume.km_run, volume.km_walk) == (6.0, 12.0)
    assert "marchés" in str(volume)


def test_an_unknown_week_is_refused_never_answered_with_zero():
    """`--semaine 31` used to match no row, print « 0.00 h-éq (0/100) », and look
    exactly like a rest week. A reader that meets a non-conforming input REFUSES."""
    known = ["2026-W30", "2026-W31"]
    assert ledger.normalize_week("31", known) == "2026-W31"
    assert ledger.normalize_week("w30", known) == "2026-W30"
    assert ledger.normalize_week("2026-W31", known) == "2026-W31"
    for bad in ["99", "2026-W29", "bidule", ""]:
        try:
            ledger.normalize_week(bad, known)
        except ValueError:
            continue
        raise AssertionError(f"« {bad} » accepté au lieu d'être refusé")


def _week_of_runs(monday: dt.date, kms: list[float]) -> list[Row]:
    return [Row(monday + dt.timedelta(days=i * 2), "easy", km=km, hours=km / 10)
            for i, km in enumerate(kms)]


def test_the_week_in_progress_is_marked_and_kept_out_of_the_slope():
    """A week still open compared with closed weeks fabricates a collapse that does
    not exist -- the error this instrument must make impossible, not reproduce."""
    rows: list[Row] = []
    monday = dt.date(2026, 7, 6)          # a Monday
    for index in range(6):
        rows += _week_of_runs(monday + dt.timedelta(weeks=index), [6.0, 6.0, 8.0])
    thursday = monday + dt.timedelta(weeks=6, days=3)
    rows += _week_of_runs(monday + dt.timedelta(weeks=6), [5.0])

    traces = ledger.trajectory(rows, thursday)
    assert traces[-1].open and traces[-1].elapsed == 4
    assert all(not trace.open for trace in traces[:-1])
    # Six closed weeks stay available despite the open week: otherwise the verdict
    # could never be computed on a review day.
    assert sum(1 for trace in traces if not trace.open) == ledger.TRAJECTORY_WEEKS
    verdict = ledger.trajectory_verdict(traces)
    assert not any("non calculable" in line for line in verdict)
    # Closed weeks all identical: the slope is zero, and the Thursday 5 km must not
    # make it drop.
    assert all("+0 %" in line for line in verdict)


def test_the_longest_of_thirty_days_is_a_landmark_not_a_ceiling():
    """No fixed percentage: the ledger gives the landmark, nothing turns it into a
    ceiling."""
    today = dt.date(2026, 8, 20)
    rows = [Row(dt.date(2026, 8, 16), "long", km=10.31, hours=1.4),
            Row(dt.date(2026, 8, 19), "easy", km=7.04, hours=0.75),
            Row(dt.date(2026, 6, 30), "long", km=22.0, hours=2.5)]   # outside the window
    assert ledger.longest_recent(rows, today) == 10.31
    assert ledger.longest_recent([], today) is None
    assert not hasattr(ledger, "next_long_run_cap")


# ------------------------------------------------ scenario 9: the slope, computed

def _long_runs(series: dict[str, float]) -> list[Row]:
    """A long run on the Sunday of each week, plus a short easy run on the Tuesday."""
    rows = []
    for label, km in series.items():
        monday = ledger._week_start(label)
        rows.append(Row(monday + dt.timedelta(days=1), "easy", km=6.0, hours=0.6))
        rows.append(Row(monday + dt.timedelta(days=6), "long", km=km, hours=km / 8))
    return rows


# The real series (journals), one unknown week set to 10.4.
SEPTEMBER = {"2026-W33": 10.31, "2026-W34": 7.0, "2026-W35": 10.41, "2026-W36": 10.4,
             "2026-W37": 11.53, "2026-W38": 10.80, "2026-W39": 10.5}


def test_the_old_three_against_three_verdict_reads_a_stall_as_a_rise():
    """The defect this block fixes: on a review day, the longest run has not moved since
    the record, yet the 3-against-3 verdict still shows about +11 %. The stall counter
    tells the truth; it triggers as soon as the week closes without a record."""
    rows = _long_runs({**SEPTEMBER, "2026-W40": 11.0})
    bilan_w40 = dt.date(2026, 10, 4)
    verdict = ledger.trajectory_verdict(ledger.trajectory(rows, bilan_w40))
    longest_line = next(line for line in verdict if line.startswith("Plus longue"))
    assert "+11 %" in longest_line
    stall = ledger.long_run_stall(rows, bilan_w40)
    assert stall is not None and stall.weeks == 2 and not stall.triggered
    stall = ledger.long_run_stall(rows, dt.date(2026, 10, 5))
    assert stall is not None and stall.triggered
    assert stall.weeks == 3 and stall.record_week == "2026-W37"
    assert "protocole d'instruction des baisses" in str(stall)


def test_a_stall_counts_closed_weeks_only_and_a_record_resets_it():
    rows = _long_runs(SEPTEMBER)
    # On a Sunday the current week is open, so only one week is closed after the record.
    stall = ledger.long_run_stall(rows, dt.date(2026, 9, 27))
    assert stall is not None and stall.weeks == 1 and not stall.triggered
    # Lundi 28/09 : W39 close, deux semaines sans record.
    assert ledger.long_run_stall(rows, dt.date(2026, 9, 28)).weeks == 2
    # A real record clears the counter; 11.6 against 11.53 is GPS noise.
    noisy = _long_runs({**SEPTEMBER, "2026-W40": 11.6})
    assert ledger.long_run_stall(noisy, dt.date(2026, 10, 5)).weeks == 3
    record = _long_runs({**SEPTEMBER, "2026-W40": 12.6})
    assert ledger.long_run_stall(record, dt.date(2026, 10, 5)).weeks == 0


def test_a_week_without_any_run_is_not_progress():
    rows = _long_runs({"2026-W36": 10.0, "2026-W37": 11.0})
    rows += _long_runs({"2026-W40": 9.0})
    stall = ledger.long_run_stall(rows, dt.date(2026, 10, 5))
    assert stall is not None and stall.weeks == 3        # W38 and W39 empty, W40 shorter


def test_the_projection_says_what_a_target_would_require_not_what_is_allowed():
    rows = _long_runs(SEPTEMBER)
    projection = ledger.race_projection(rows, dt.date(2026, 9, 27), dt.date(2026, 11, 26))
    assert projection is not None
    assert projection.start_km == 11.53
    # Six weeks ending before the taper, which starts two weeks before the race.
    assert projection.weeks == [
        "2026-W40", "2026-W41", "2026-W42", "2026-W43", "2026-W44", "2026-W45"]
    assert projection.taper_from == "2026-W46"
    assert round(projection.required_growth(30.0), 3) == round((30 / 11.53) ** (1 / 6), 3)
    assert projection.required_growth(10.0) == 1.0
    assert projection.required_growth(30.0, lost=6) is None
    lines = ledger.projection_lines(projection, target_km=30.0)
    assert any("+17 %/sem" in line and "+21 %/sem" in line for line in lines)
    assert not any("10 %" in line for line in lines)
    assert "--cible-sl" in ledger.projection_lines(projection)[-1]
    assert ledger.race_projection(rows, dt.date(2026, 12, 1), dt.date(2026, 11, 26)) is None


def test_the_race_date_comes_from_a_typed_race_deadline_only():
    class Store:
        def deadline_rows(self):
            return [
                {"kind": "general", "status": "open", "due_on": dt.date(2026, 10, 4)},
                {"kind": "race", "status": "done", "due_on": dt.date(2026, 9, 1)},
                {"kind": "race", "status": "open", "due_on": "2026-11-26"},
            ]
    today = dt.date(2026, 9, 27)
    assert ledger.next_race_date(Store(), today) == dt.date(2026, 11, 26)
    Store.deadline_rows = lambda self: []  # type: ignore[method-assign]
    assert ledger.next_race_date(Store(), today) is None


def test_a_planned_run_shows_its_gap_to_the_month_without_any_verdict():
    rows = _long_runs(SEPTEMBER)
    sessions = [
        {"id": "sl-dim", "kind": "run", "status": "planned", "date": "2026-10-04",
         "distance_cap_km": 14.0},
        {"id": "ef-mar", "kind": "run", "status": "cancelled", "date": "2026-09-29",
         "distance_cap_km": 7.0},
        {"id": "renfo", "kind": "strength", "status": "planned", "date": "2026-09-30"},
        {"id": "ef-passee", "kind": "run", "status": "planned", "date": "2026-09-20",
         "distance_cap_km": 7.0},
    ]
    notes = ledger.transition_notes(rows, sessions, dt.date(2026, 9, 27))
    assert notes == ["sl-dim 14.0 km = +21 % de 11.5 km (plus longue des 30 j)"]


def test_an_undeclared_week_is_neither_ok_nor_multiplied():
    # A review written "sleep ok, normal load" for a week with no declaration at all.
    class Store:
        def weekly_instrument_rows(self):
            return [{"iso_week": "2026-W39", "sleep": None, "life_load": None}]

        def daily_rows(self):
            return [{"checked_on": dt.date(2026, 10, 8), "sleep": "good",
                     "life_load": None, "desire": None}]

    loaded = ledger.load_instruments(Store())
    assert loaded["2026-W39"] == Instruments(sleep=None, life_load=None)
    assert loaded["2026-W41"] == Instruments(sleep="ok", life_load=None)
    rows = _etalon_rows()
    assert ledger.equivalent_hours(rows, Instruments(), RPE_TARGETS)[0] == \
        ledger.equivalent_hours(rows, Instruments(sleep="ok", life_load="normal"),
                                RPE_TARGETS)[0]


ZONES = [ledger.PainZone(ZONE, "Genou")]


def _gate_rows(*, peak_before=1.0, peak_now=1.0, wake_now=(1.0,)):
    rows = [Row(dt.date(2026, 9, 1), "wake-trace", pains=zoned(1.0)),
            Row(dt.date(2026, 9, 29), "easy", km=8, hours=0.8, pains=zoned(peak_before)),
            Row(dt.date(2026, 10, 6), "easy", km=8, hours=0.8, pains=zoned(peak_now))]
    rows += [Row(dt.date(2026, 10, 7 + i), "wake-trace", pains=zoned(value))
             for i, value in enumerate(wake_now)]
    return rows


def test_the_gate_is_green_when_every_case_is_established():
    lines = ledger.gate(_gate_rows(), "2026-W41", Instruments(sleep="ok"), ZONES)
    assert lines[0] == "Porte (règle po, calculée) : verte"


def test_a_rise_of_half_a_point_closes_the_gate():
    lines = ledger.gate(_gate_rows(peak_now=1.5), "2026-W41", Instruments(sleep="ok"),
                        ZONES)
    assert lines[0].endswith(": rouge")
    assert "  ✗ tendance : 1 → 1.5" in lines


def test_a_wake_told_in_prose_is_not_a_measured_wake():
    # "Wake readings at baseline" read in prose, no numeric value in the ledger.
    lines = ledger.gate(_gate_rows(wake_now=(None, None)), "2026-W41",
                        Instruments(sleep="ok"), ZONES)
    assert lines[0].endswith(": incomplète")
    assert any(line.startswith("  ? réveils : 0 chiffré(s) sur 2") for line in lines)


def test_an_undeclared_sleep_leaves_the_gate_incomplete_not_green():
    lines = ledger.gate(_gate_rows(), "2026-W41", Instruments(), ZONES)
    assert lines[0].endswith(": incomplète")
    assert "  ? sommeil non déclaré" in lines


# ------------------------------------------------------------- several zones

def test_two_zones_never_share_a_series():
    """A rise in one zone is never read as a rise in another."""
    rows = [Row(dt.date(2026, 7, 29), "easy", pains={"talon": 3.0, "cheville": 0.5}),
            Row(dt.date(2026, 7, 30), "wake-trace", pains={"talon": 2.0, "cheville": 0.0})]
    assert ledger.pain_trend(rows, 1, "talon")[0].effort.peak == 3.0
    assert ledger.pain_trend(rows, 1, "cheville")[0].effort.peak == 0.5
    assert [flag.kind for flag in ledger.esculier_check(rows, "cheville")] == []
    assert "AU-DESSUS DU PLAFOND" in [flag.kind for flag in ledger.esculier_check(rows, "talon")]


def test_the_gate_judges_each_zone_and_names_it():
    zones = [ledger.PainZone("talon", "Talon"), ledger.PainZone("cheville", "Cheville")]
    rows = [Row(dt.date(2026, 9, 1), "wake-trace", pains={"talon": 1.0, "cheville": 1.0}),
            Row(dt.date(2026, 9, 29), "easy", km=8, hours=0.8, pains={"talon": 1.0, "cheville": 1.0}),
            Row(dt.date(2026, 10, 6), "easy", km=8, hours=0.8, pains={"talon": 1.0, "cheville": 1.5}),
            Row(dt.date(2026, 10, 7), "wake-trace", pains={"talon": 1.0, "cheville": 1.0})]
    lines = ledger.gate(rows, "2026-W41", Instruments(sleep="ok"), zones)
    assert lines[0].endswith(": rouge")
    assert "  ✗ Cheville · tendance : 1 → 1.5" in lines
    assert "  ✓ Talon · tendance : 1 → 1" in lines


def test_without_a_watched_zone_the_gate_has_no_pain_case():
    rows = [Row(dt.date(2026, 10, 6), "easy", km=8, hours=0.8)]
    lines = ledger.gate(rows, "2026-W41", Instruments(sleep="ok"), [])
    assert not any("pic d'effort" in line or "réveils" in line for line in lines)
