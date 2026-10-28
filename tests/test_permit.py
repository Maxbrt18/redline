"""Easy-run permit: the state is computed from the sealed choices and the ledger."""

import datetime as dt

from redline.model import permit

MON = dt.date(2026, 9, 28)
#: Top of Z2 for the test athlete: what `Instance.hr_bounds[1]` provides.
HR_CAP = 157
PERIOD = permit.Window(MON, "2026-W42")


def _choice(key, day, minutes):
    return {"session_key": key, "scheduled_on": day, "coach_minutes": minutes,
            "iso_week": permit.iso_week(day)}


def _ef(day, minutes, hr=150, strava_id=None):
    return permit.Run(day, "easy", minutes, hr, strava_id)


def test_a_run_ef_is_compared_to_the_sealed_choice():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 47)],
                            {tue + dt.timedelta(days=1): 1}, {"w40-mar-ef": 55}, {},
                            today=tue + dt.timedelta(days=2), hr_cap=HR_CAP)
    line = lines[0]
    assert line.status == "courue"
    assert round(line.gap) == 7 and line.agrees is True
    assert line.next_trace == 1 and line.triggers == []


def test_a_skipped_ef_is_a_valid_choice_not_a_trigger():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [], {}, {}, {},
                            today=tue + dt.timedelta(days=1), hr_cap=HR_CAP)
    assert lines[0].status == "sautée" and lines[0].triggers == []


def test_net_trace_envelope_and_heart_rate_trigger_withdrawal():
    tue, thu = MON + dt.timedelta(days=1), MON + dt.timedelta(days=3)
    lines = permit.evaluate(
        [_choice("w40-mar-ef", tue, 40), _choice("w40-jeu-ef", thu, 45)],
        [_ef(tue, 45), _ef(thu, 62, hr=161)],
        {tue + dt.timedelta(days=1): 2}, {"w40-mar-ef": 55, "w40-jeu-ef": 55}, {},
        today=thu + dt.timedelta(days=1), hr_cap=HR_CAP)
    assert lines[0].triggers == ["trace nette le lendemain"]
    assert len(lines[1].triggers) == 2
    current = permit.state(lines, [], today=thu + dt.timedelta(days=1), period=PERIOD)
    assert current.phase.startswith("retrait")
    assert current.withdrawal_until == thu + dt.timedelta(days=permit.WITHDRAWAL_DAYS)


def test_declared_life_load_and_group_restrict_the_week():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 40, strava_id=7)],
                            {}, {}, {7: True}, today=tue + dt.timedelta(days=1), hr_cap=HR_CAP)
    current = permit.state(lines, [{"checked_on": MON, "life_load": "elevee"}],
                           today=tue + dt.timedelta(days=1), period=PERIOD)
    reasons = current.restricted_weeks["2026-W40"]
    assert any("charge de vie" in reason for reason in reasons)
    assert any("groupe" in reason for reason in reasons)


def test_obtention_is_judged_after_w42_on_five_clean_efs():
    days = [MON + dt.timedelta(days=offset) for offset in (1, 3, 8, 10, 15)]
    choices = [_choice(f"ef-{index}", day, 40) for index, day in enumerate(days)]
    runs = [_ef(day, 42) for day in days]
    after = permit.week_end(PERIOD.obtention_week) + dt.timedelta(days=1)
    lines = permit.evaluate(choices, runs, {}, {}, {}, today=after, hr_cap=HR_CAP)
    current = permit.state(lines, [], today=after, period=PERIOD)
    assert current.obtention["obtenu"] is True and current.phase == "permis"
    rendered = "\n".join(permit.render(lines, current))
    assert "OBTENU" in rendered and "parcours neuf" in rendered


def test_before_w42_the_counter_is_shown_and_nothing_is_granted():
    tue = MON + dt.timedelta(days=1)
    lines = permit.evaluate([_choice("w40-mar-ef", tue, 40)], [_ef(tue, 40)], {}, {}, {},
                            today=tue + dt.timedelta(days=1), hr_cap=HR_CAP)
    current = permit.state(lines, [], today=tue + dt.timedelta(days=1), period=PERIOD)
    assert current.phase == "conduite accompagnée" and current.obtention is None
    assert any("1 EF courues sur 5" in line for line in permit.render(lines, current))


def test_the_window_starts_with_the_first_sealed_choice():
    """No date in the code: the permit opens on the Monday of the first sealed choice and
    is judged after `PERMIT_WEEKS` weeks, that one included."""
    choices = [_choice("w41-jeu-ef", MON + dt.timedelta(days=10), 40),
               _choice("w40-mer-ef", MON + dt.timedelta(days=2), 40)]
    assert permit.window(choices) == PERIOD
    assert permit.window([]) is None
