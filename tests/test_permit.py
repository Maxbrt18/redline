"""Easy-run permit: the state is computed from the sealed choices and the ledger."""

import dataclasses
import datetime as dt

import pytest

from redline.analysis import permit_report
from redline.model import iso_week, permit, rulebook

MON = dt.date(2026, 9, 28)
#: Top of Z2 for the test athlete: what `Instance.hr_bounds[1]` provides.
HR_CAP = 157
PERIOD = permit.Window(MON, "2026-W42")


def _choice(key, day, minutes):
    return {"session_key": key, "scheduled_on": day, "coach_minutes": minutes,
            "iso_week": iso_week.of(day)}


def _ef(day, minutes, hr=150, strava_id=None):
    return permit.Run(day, "easy", minutes, hr, strava_id)


def test_a_run_ef_is_compared_to_the_sealed_choice():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 47)],
                            {tue + dt.timedelta(days=1): 1}, {"w40-mar-ef": 55}, {},
                            today=tue + dt.timedelta(days=2), hr_cap=HR_CAP)
    line = lines[0]
    assert line.status == "run"
    assert round(line.gap) == 7 and line.agrees is True
    assert line.next_trace == 1 and line.triggers == []


def test_a_skipped_ef_is_a_valid_choice_not_a_trigger():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [], {}, {}, {},
                            today=tue + dt.timedelta(days=1), hr_cap=HR_CAP)
    assert lines[0].status == "skipped" and lines[0].triggers == []


def test_net_trace_envelope_and_heart_rate_trigger_withdrawal():
    tue, thu = MON + dt.timedelta(days=1), MON + dt.timedelta(days=3)
    lines = permit.evaluate(
        [_choice("w40-mar-ef", tue, 40), _choice("w40-jeu-ef", thu, 45)],
        [_ef(tue, 45), _ef(thu, 62, hr=161)],
        {tue + dt.timedelta(days=1): 2}, {"w40-mar-ef": 55, "w40-jeu-ef": 55}, {},
        today=thu + dt.timedelta(days=1), hr_cap=HR_CAP)
    assert lines[0].triggers == ["trace au-dessus de la base le lendemain"]
    assert len(lines[1].triggers) == 2
    current = permit.state(lines, [], today=thu + dt.timedelta(days=1), period=PERIOD)
    assert current.phase == "withdrawn"
    assert current.withdrawal_until == thu + dt.timedelta(days=permit.thresholds().withdrawal_days)


def test_declared_life_load_and_group_restrict_the_week():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 40, strava_id=7)],
                            {}, {}, {7: True}, today=tue + dt.timedelta(days=1), hr_cap=HR_CAP)
    current = permit.state(lines, [{"checked_on": MON, "life_load": "high"}],
                           today=tue + dt.timedelta(days=1), period=PERIOD)
    reasons = current.restricted_weeks["2026-W40"]
    assert any("charge de vie" in reason for reason in reasons)
    assert any("groupe" in reason for reason in reasons)


def test_the_grant_is_judged_after_w42_on_five_clean_easy_runs():
    days = [MON + dt.timedelta(days=offset) for offset in (1, 3, 8, 10, 15)]
    choices = [_choice(f"ef-{index}", day, 40) for index, day in enumerate(days)]
    runs = [_ef(day, 42) for day in days]
    after = iso_week.sunday(PERIOD.granted_week) + dt.timedelta(days=1)
    lines = permit.evaluate(choices, runs, {}, {}, {}, today=after, hr_cap=HR_CAP)
    current = permit.state(lines, [], today=after, period=PERIOD)
    assert current.grant["granted"] is True and current.phase == "granted"
    rendered = "\n".join(permit.render(lines, current))
    assert "OBTENU" in rendered and "parcours neuf" in rendered


def test_before_w42_the_counter_is_shown_and_nothing_is_granted():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 40)], {}, {}, {},
                            today=tue + dt.timedelta(days=1), hr_cap=HR_CAP)
    current = permit.state(lines, [], today=tue + dt.timedelta(days=1), period=PERIOD)
    assert current.phase == "supervised" and current.grant is None
    assert any("1 EF courues sur 5" in line for line in permit.render(lines, current))


def test_the_window_starts_with_the_first_sealed_choice():
    """No date in the code: the permit opens on the Monday of the first sealed choice and
    is judged after `permit_weeks` weeks, that one included."""
    choices = [_choice("w41-jeu-ef", MON + dt.timedelta(days=10), 40),
               _choice("w40-mer-ef", MON + dt.timedelta(days=2), 40)]
    assert permit.window(choices) == PERIOD
    assert permit.window([]) is None


@pytest.fixture
def graded(monkeypatch):
    """The default rulebook's graded thresholds on the fixture athlete's permit."""
    book = rulebook.current()
    graded_book = dataclasses.replace(book, parameters=dataclasses.replace(
        book.parameters, permit_tolerance_min=3, permit_tolerance_pct=5.0,
        permit_minor_pct=15.0, permit_minor_hr_bpm=6, permit_failure_withdrawal_days=7))
    monkeypatch.setattr(rulebook, "current", lambda: graded_book)


def _judged(minutes, hr=150, cap=50):
    tue = MON + dt.timedelta(days=1)
    [line] = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, minutes, hr=hr)],
                             {}, {"w40-mar-ef": cap}, {}, today=tue + dt.timedelta(days=1),
                             hr_cap=HR_CAP)
    return line


def test_without_graded_thresholds_any_run_past_its_envelope_fails():
    line = _judged(50.5)
    assert line.triggers == ["au-delà de l'enveloppe (50 min)"] and line.minors == []


def test_graded_thresholds_tolerate_then_grade_the_envelope(graded):
    assert _judged(53).triggers == [] and _judged(53).minors == []
    minor = _judged(57)
    assert minor.minors == ["un peu au-delà de l'enveloppe (50 min)"] and minor.triggers == []
    assert _judged(58).triggers == ["au-delà de l'enveloppe (50 min)"]


def test_graded_thresholds_grade_the_heart_rate_and_never_call_it_too_fast(graded):
    assert _judged(45, hr=162).minors == ["FC moyenne un peu au-dessus du plafond (162 > 157)"]
    assert _judged(45, hr=164).triggers == ["FC moyenne au-dessus du plafond (164 > 157)"]


def test_a_second_minor_gap_fails_and_a_failure_withdraws_for_a_shorter_time(graded):
    tue, thu = MON + dt.timedelta(days=1), MON + dt.timedelta(days=3)
    lines = permit.evaluate(
        [_choice("w40-mar-ef", tue, 40), _choice("w40-jeu-ef", thu, 40)],
        [_ef(tue, 56), _ef(thu, 45, hr=160)], {}, {"w40-mar-ef": 50, "w40-jeu-ef": 50}, {},
        today=thu + dt.timedelta(days=1), hr_cap=HR_CAP)
    assert lines[0].minors and not lines[0].triggers
    assert lines[1].triggers == ["deuxième écart mineur de la période"]
    current = permit.state(lines, [], today=thu + dt.timedelta(days=1), period=PERIOD)
    assert current.withdrawal_until == thu + dt.timedelta(days=7)
    rendered = "\n".join(permit.render(lines, current))
    assert "(mineur : un peu au-delà de l'enveloppe (50 min))" in rendered


def test_a_trace_above_the_base_still_withdraws_for_the_full_time(graded):
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 40)],
                            {tue + dt.timedelta(days=1): 2}, {}, {},
                            today=tue + dt.timedelta(days=2), hr_cap=HR_CAP)
    assert lines[0].safety
    current = permit.state(lines, [], today=tue + dt.timedelta(days=2), period=PERIOD)
    assert current.withdrawal_until == tue + dt.timedelta(days=14)


def test_an_enveloped_run_without_a_sealed_choice_is_judged_too():
    """Once the permit is granted the coach seals no choice: the envelope still holds."""
    tue, thu = MON + dt.timedelta(days=1), MON + dt.timedelta(days=3)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 40), _ef(thu, 74)],
                            {}, {}, {}, today=thu + dt.timedelta(days=1), hr_cap=HR_CAP,
                            envelopes=[permit.Envelope("w40-jeu-ef", thu, 50),
                                       permit.Envelope("w40-mar-ef", tue, 50)])
    assert [line.session_key for line in lines] == ["w40-mar-ef", "w40-jeu-ef"]
    free = lines[1]
    assert free.coach_minutes is None and free.gap is None and free.agrees is None
    assert free.triggers == ["au-delà de l'enveloppe (50 min)"]
    current = permit.state(lines, [], today=thu + dt.timedelta(days=1), period=PERIOD)
    assert any("enveloppe 50 min, couru 74 min" in line for line in permit.render(lines, current))


class _Plans:
    def __init__(self, sessions):
        self.sessions = sessions

    def load_plan(self, week):
        return {"revisions": [{"sessions": self.sessions}]} if week == "2026-W40" else None


def test_the_report_reads_the_envelope_of_every_easy_run_planned_since_the_start():
    sessions = [
        {"id": "w40-mar-ef", "workout": "easy", "status": "done", "date": "2026-09-29",
         "duration_cap_min": 50},
        {"id": "w40-jeu-ef", "workout": "easy", "status": "cancelled", "date": "2026-10-01",
         "duration_cap_min": 50},
        {"id": "w40-sam-sl", "workout": "long", "status": "planned", "date": "2026-10-03",
         "duration_cap_min": 120},
        {"id": "w40-dim-ef", "workout": "easy", "status": "planned", "date": "2026-10-04"}]
    caps, envelopes = permit_report._plans(_Plans(sessions), {"2026-W40", "2026-W41"})
    assert caps["w40-sam-sl"] == 120 and caps["w40-dim-ef"] is None
    assert envelopes == [permit.Envelope("w40-mar-ef", dt.date(2026, 9, 29), 50)]
    assert permit_report._weeks(MON, MON + dt.timedelta(days=15)) == {
        "2026-W40", "2026-W41", "2026-W42"}
