"""Les écarts calculés d'une sortie (audit du 2026-10-11)."""

import datetime as dt

from scripts.ledger import Row
from scripts.model import debrief
from scripts.signals import HrDrift


def test_planned_versus_done_names_every_typed_target():
    row = Row(date=dt.date(2026, 10, 3), type="SL", km=15.84, hours=1.72, hr_avg=164,
              rpe=5)
    session = {"distance_cap_km": 16.0, "hr_cap_bpm": 157, "rpe_min": 4, "rpe_max": 4.5}
    assert debrief.gaps(row, session, None) == [
        "distance 15,84 km pour un plafond de 16 (-0,16)",
        "FC moyenne 164 pour un plafond de 157 (+7)",
        "RPE 5 pour une cible 4-4,5",
    ]


def test_a_duration_cap_hidden_behind_a_distance_is_never_a_gap():
    """15/10 : « 57 min pour 55 » reproché sur un plafond qu'aucun message n'avait
    donné, le rendu taisant la durée dès qu'une distance engage."""
    row = Row(date=dt.date(2026, 10, 15), type="seuil", km=9.2, hours=0.95)
    seuil = {"distance_cap_km": 9.0, "duration_cap_min": 55}
    assert debrief.gaps(row, seuil, None) == ["distance 9,2 km pour un plafond de 9 (+0,2)"]
    ef = {"duration_cap_min": 50}
    assert debrief.gaps(row, ef, None) == ["durée 57 min pour un plafond de 50 (+7)"]


def test_heart_rate_at_comparable_pace_comes_last_and_without_a_plan():
    row = Row(date=dt.date(2026, 10, 6), type="EF", km=8.0, hours=0.82, hr_avg=158)
    drift = HrDrift(dt.date(2026, 10, 6), 368, 158.0, 149.0, 5)
    assert debrief.gaps(row, None, drift) == [
        "FC à allure comparable : 06/10 à 6:08/km : 158 bpm contre 149 "
        "(médiane de 5 EF voisines, +9)"]


def test_a_strapless_heart_rate_is_never_held_against_its_cap():
    """07/10 : « la FC n'est pas à regarder, je n'avais pas de capteur » (2026-10-20)."""
    session = {"hr_cap_bpm": 157}
    strapless = Row(date=dt.date(2026, 10, 7), type="EF", hr_avg=162, hr_doubt="sans_brassard")
    assert debrief.gaps(strapless, session, None) == []
    meal = Row(date=dt.date(2026, 10, 6), type="EF", hr_avg=162, hr_doubt="contexte")
    assert debrief.gaps(meal, session, None) == ["FC moyenne 162 pour un plafond de 157 (+5)"]
