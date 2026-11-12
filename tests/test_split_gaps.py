"""The kilometres of an easy or long run, read against the rulebook's thresholds."""

from __future__ import annotations

import dataclasses
import datetime as dt

from redline.analysis import ledger
from redline.model import debrief, rulebook
from redline.model.debrief import SplitGapKind
from redline.model.vocabulary import HrDoubt


def km(n, pace, hr=140, dist=1000, dplus=0):
    return {"n": n, "dist_m": dist, "moving_s": round(pace * dist / 1000), "pace_s": pace,
            "hr": hr, "cadence": 168, "dplus": dplus}


def even(count, pace=330, hr=140):
    return [km(n, pace, hr) for n in range(1, count + 1)]


def kinds(gaps):
    return [gap.kind for gap in gaps]


def test_kilometres_above_the_session_cap_are_named_with_their_peak():
    rows = [km(1, 330, 150), km(2, 330, 158), km(3, 330, 152), km(4, 330, 161)]
    [gap] = debrief.split_gaps(rows, 157)
    assert gap == debrief.SplitGap(SplitGapKind.HR_ABOVE_CAP, 161.0, 157.0, (2, 4))
    assert debrief.render(gap) == "FC au-dessus du plafond de 157 sur 2 km (km 2 et 4), jusqu'à 161"


def test_no_cap_or_a_kilometre_within_the_margin_names_nothing(monkeypatch):
    rows = [km(1, 330, 158)]
    assert debrief.split_gaps(rows, None) == []
    book = rulebook.current()
    monkeypatch.setattr(rulebook, "current", lambda: dataclasses.replace(
        book, parameters=dataclasses.replace(book.parameters, split_hr_margin_bpm=2)))
    assert debrief.split_gaps(rows, 157) == []


def test_a_first_kilometre_well_ahead_of_the_others_is_a_fast_start():
    rows = [km(1, 300)] + [km(n, 330) for n in range(2, 6)]
    [gap] = debrief.split_gaps(rows, None)
    assert gap.kind is SplitGapKind.FAST_START
    assert debrief.render(gap) == "départ rapide : 1er km en 5:00 pour 5:30 sur les autres (-9 %)"
    assert debrief.split_gaps([km(1, 320), *even(5)[1:]], None) == []


def test_a_last_third_slower_than_the_rest_is_a_fade():
    rows = [*even(4), km(5, 360), km(6, 365)]
    # Slower at the same HR is also a lower speed-to-HR ratio: the decoupling follows.
    gap, decoupling = debrief.split_gaps(rows, None)
    assert decoupling.kind is SplitGapKind.DECOUPLING
    assert gap.kind is SplitGapKind.FADE and gap.kms == (5, 6)
    assert debrief.render(gap) == "fin qui faiblit : km 5 et 6 en 6:02 contre 5:30 avant (+10 %)"


def test_hr_climbing_at_the_same_pace_is_a_decoupling():
    rows = even(3, hr=140) + [km(n, 330, 150) for n in range(4, 7)]
    [gap] = debrief.split_gaps(rows, None)
    assert gap.kind is SplitGapKind.DECOUPLING and round(gap.value, 1) == 6.7
    assert debrief.render(gap) == "découplage allure/FC de 6,7 % entre les deux moitiés"
    assert debrief.split_gaps(rows, None, hr_trusted=False) == []


def test_an_even_run_or_too_few_kilometres_shows_no_shape_gap():
    assert debrief.split_gaps(even(8), 157) == []
    assert debrief.split_gaps([km(1, 290), km(2, 330), km(3, 380)], None) == []


def test_a_threshold_absent_from_the_rulebook_reads_no_gap(monkeypatch):
    book = rulebook.current()
    monkeypatch.setattr(rulebook, "current", lambda: dataclasses.replace(
        book, parameters=dataclasses.replace(
            book.parameters, split_hr_margin_bpm=None, split_fast_start_pct=None,
            split_fade_pct=None, split_decoupling_pct=None)))
    rows = [km(1, 290, 170)] + even(3, hr=140)[1:] + [km(n, 380, 165) for n in range(4, 7)]
    assert debrief.split_gaps(rows, 157) == []


def row(kind="easy", hr_doubt=None):
    return ledger.Row(date=dt.date(2026, 10, 3), type=kind, idempotency_key="strava:42",
                      km=6.0, hours=0.55, hr_avg=150, dplus=20, hr_doubt=hr_doubt)


def test_the_debrief_gaps_read_the_kilometres_of_easy_and_long_runs_only():
    rows = [km(1, 300, 160), *even(5)[1:]]
    easy = {"workout": "easy", "hr_cap_bpm": 157}
    out = debrief.gaps(row(), easy, None, rows)
    assert any(text.startswith("FC au-dessus du plafond de 157") for text in out)
    assert any(text.startswith("départ rapide") for text in out)
    threshold = {"workout": "threshold", "hr_cap_bpm": 157}
    assert not any("plafond de 157 sur" in text or "départ" in text
                   for text in debrief.gaps(row("threshold"), threshold, None, rows))
    # An unplanned easy run is read from its ledger kind, without a cap.
    assert [text for text in debrief.gaps(row(), None, None, rows)
            if text.startswith("départ rapide")]


def test_a_wrist_hr_without_the_armband_is_never_held_to_the_cap():
    rows = even(3, hr=160) + [km(n, 330, 175) for n in range(4, 7)]
    out = debrief.gaps(row(hr_doubt=HrDoubt.NO_SENSOR), {"workout": "long", "hr_cap_bpm": 157},
                       None, rows)
    assert not any("FC" in text or "découplage" in text for text in out)
