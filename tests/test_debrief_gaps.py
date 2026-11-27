"""The computed gaps of a run."""

import datetime as dt

from redline.analysis.ledger import Row
from redline.analysis.signals import HrDrift
from redline.model import debrief


def test_planned_versus_done_names_every_typed_target_and_never_an_rpe_band():
    """A band carried from an old revision is no target: the RPE is read against the
    range its dose expects, in the verdict."""
    row = Row(date=dt.date(2026, 10, 3), type="long", km=15.84, hours=1.72, hr_avg=164,
              rpe=5)
    session = {"distance_cap_km": 16.0, "hr_cap_bpm": 157, "rpe_min": 4, "rpe_max": 4.5}
    assert debrief.gaps(row, session, None) == [
        "distance 15,84 km pour un plafond de 16 (-0,16)",
        "FC moyenne 164 pour un plafond de 157 (+7)",
    ]


def test_a_duration_cap_hidden_behind_a_distance_is_never_a_gap():
    """"57 min for 55" was reproached on a cap that no message had given, the rendering
        omitting the duration as soon as a distance is committed."""
    row = Row(date=dt.date(2026, 10, 15), type="threshold", km=9.2, hours=0.95)
    threshold = {"distance_cap_km": 9.0, "duration_cap_min": 55}
    assert debrief.gaps(row, threshold, None) == ["distance 9,2 km pour un plafond de 9 (+0,2)"]
    ef = {"duration_cap_min": 50}
    assert debrief.gaps(row, ef, None) == ["durée 57 min pour un plafond de 50 (+7)"]


def test_heart_rate_at_comparable_pace_comes_last_and_without_a_plan():
    row = Row(date=dt.date(2026, 10, 6), type="easy", km=8.0, hours=0.82, hr_avg=158)
    drift = HrDrift(dt.date(2026, 10, 6), 368, 158.0, 149.0, 5)
    assert debrief.gaps(row, None, drift) == [
        "FC à allure comparable : 06/10 à 6:08/km : 158 bpm contre 149 "
        "(médiane de 5 EF voisines, +9)"]


def test_a_strapless_heart_rate_is_never_held_against_its_cap():
    """A heart rate recorded without the armband is never held against its cap."""
    session = {"hr_cap_bpm": 157}
    strapless = Row(date=dt.date(2026, 10, 7), type="easy", hr_avg=162, hr_doubt="no_sensor")
    assert debrief.gaps(strapless, session, None) == []
    meal = Row(date=dt.date(2026, 10, 6), type="easy", hr_avg=162, hr_doubt="context")
    assert debrief.gaps(meal, session, None) == ["FC moyenne 162 pour un plafond de 157 (+5)"]


def test_an_easy_run_faster_within_its_duration_is_form_not_a_gap():
    """25 min and 4 km at most prescribe 6:15/km: run in 25 min at the same heart rate, the
    kilometres past 4 are the pace, never a fault."""
    session = {"workout": "easy", "duration_cap_min": 25, "distance_cap_km": 4.0,
               "hr_cap_bpm": 150}
    faster = Row(date=dt.date(2030, 3, 14), type="easy", km=4.98, hours=25 / 60, hr_avg=149)
    [line, hr] = debrief.gaps(faster, session, None)
    assert line.startswith("durée tenue (25 min pour 25 au plus) et 4,98 km au lieu de 4")
    assert "jamais un écart" in line and not hr.startswith("distance")
    longer = Row(date=dt.date(2030, 3, 14), type="easy", km=4.98, hours=31 / 60, hr_avg=149)
    assert debrief.gaps(longer, session, None)[0] == \
        "distance 4,98 km pour un plafond de 4 (+0,98)", "past both bounds, it ran more"
