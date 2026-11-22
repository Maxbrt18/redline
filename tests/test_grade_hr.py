"""The athlete's easy HR by grade, dated, from the stretches of solo easy runs."""

import datetime as dt

import pytest

from redline.analysis import easy_load, grade_hr, signals
from redline.evaluation.debrief_bench import synthetic_streams

DAY = dt.date(2030, 3, 18)


def stretches(*legs):
    """The stretches of a synthetic run: legs of [metres, s/km, grade %, cadence, HR]."""
    shape = easy_load.profile(synthetic_streams([list(leg) for leg in legs]))
    assert shape is not None
    return shape.stretches


def hilly(climb_hr=155, cadence=85):
    return stretches((1200, 374, 0, 85, 140), (800, 480, 6, cadence, climb_hr))


@pytest.mark.parametrize(("grade", "centre"), [
    (-1.5, None), (-0.9, 0), (0.99, 0), (1.0, 2), (3.2, 4), (12.4, 12), (31, None)])
def test_a_grade_falls_in_the_bin_of_its_nearest_even_grade(grade, centre):
    assert grade_hr.bin_of(grade) == centre


def test_each_bin_is_the_median_hr_of_its_run_stretches():
    runs = [(DAY - dt.timedelta(days=offset), hilly(climb_hr)) for offset, climb_hr
            in ((2, 154), (9, 156), (16, 158))]
    found = grade_hr.curve(runs, DAY)
    assert found is not None and found.runs == 3
    assert found.hr_at(0.4) == pytest.approx(140, abs=1)
    assert found.hr_at(6.3) == pytest.approx(156, abs=2)
    assert found.hr_at(10) is None, "no grade is interpolated"
    assert found.hr_at(-4) is None, "descents are left out"


def test_walked_stretches_old_runs_and_thin_bins_are_left_out():
    walked = [(DAY - dt.timedelta(days=offset), hilly(cadence=55)) for offset in (2, 9, 16)]
    found = grade_hr.curve(walked, DAY)
    assert found is not None and found.hr_at(6) is None
    old = [(DAY - dt.timedelta(weeks=7), hilly()), (DAY - dt.timedelta(days=2), hilly()),
           (DAY - dt.timedelta(days=9), hilly())]
    assert grade_hr.curve(old, DAY) is None, "two runs within six weeks keep no bin"
    assert grade_hr.curve([], DAY) is None


def test_the_curve_reads_solo_runs_only(monkeypatch):
    runs = [(DAY - dt.timedelta(days=offset), easy_load.Profile(hilly(), 2000, 48, 0), group)
            for offset, group in ((2, False), (9, False), (16, False), (23, True), (30, None))]
    monkeypatch.setattr(easy_load, "easy_runs", lambda store, rows, day: runs)
    found = grade_hr.curve_on(None, [], DAY)
    assert found is not None and found.runs == 3, "a group run, or an unknown one, is out"


def test_the_signals_show_the_curve():
    runs = [(DAY - dt.timedelta(days=offset), hilly()) for offset in (2, 9, 16)]
    found = grade_hr.curve(runs, DAY)
    shown = signals.render([], ["2030-W10", "2030-W11"], frozenset(), DAY, found)
    assert "FC en EF par pente         0 % 140 · 6 % 155 (3 EF seules, six semaines)" in shown
    assert "pas assez d'EF seules" in signals.render([], ["2030-W11"], frozenset(), DAY)
