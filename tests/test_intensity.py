"""An intensity rendered from its reference: a pace, an effort or heart rates."""

from __future__ import annotations

import pytest

from redline.model.cycle_templates import Range
from redline.model.intensity import References, render
from redline.model.vocabulary import IntensityRef

#: CS of 4 m/s (4:10/km), the first threshold at 82 % of it, a goal at 3.5 m/s.
REFS = References(critical_speed_mps=4.0, first_threshold_mps=3.28, goal_speed_mps=3.5,
                  hr_rest=50, hr_max=190)


def test_a_speed_reference_renders_a_pace_band_in_percent_of_its_speed_slowest_first():
    rendered = render(IntensityRef.CRITICAL_SPEED, Range(90, 95), REFS)
    assert rendered.pace_s == pytest.approx(Range(1000 / 3.6, 1000 / 3.8))
    assert render(IntensityRef.GOAL_PACE, Range(100, 100), REFS).pace_s.low == \
        pytest.approx(1000 / 3.5)
    assert render(IntensityRef.FIRST_THRESHOLD, Range(100, 100), REFS).pace_s.low == \
        pytest.approx(1000 / 3.28)


def test_a_speed_reference_without_its_speed_waits_for_a_calibration():
    rendered = render(IntensityRef.CRITICAL_SPEED, Range(90, 95), References())
    assert (rendered.pace_s, rendered.rpe, rendered.hr_bpm) == (None, None, None)


def test_an_effort_renders_as_itself():
    assert render(IntensityRef.RPE, Range(7, 8), References()).rpe == Range(7, 8)


def test_heart_rate_zones_render_from_the_reserve():
    assert render(IntensityRef.HR_ZONE, Range(1, 2), REFS).hr_bpm == Range(50, 148)
    assert render(IntensityRef.HR_ZONE, Range(4, 5), REFS).hr_bpm == Range(162, 190)
    assert render(IntensityRef.HR_ZONE, Range(1, 2), References()).hr_bpm is None
