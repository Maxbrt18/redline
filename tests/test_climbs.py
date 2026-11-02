"""The climbs of a route: existence, boundaries, gain, grade sections, robustness to noise."""

from __future__ import annotations

import numpy as np
import pytest

from redline.analysis import climbs


def _route(*legs: tuple[float, float], step_m: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """A fictional route from (length in m, grade in %) legs, starting at 200 m."""
    distance = [0.0]
    elevation = [200.0]
    for length, grade in legs:
        for _ in range(round(length / step_m)):
            distance.append(distance[-1] + step_m)
            elevation.append(elevation[-1] + step_m * grade / 100)
    return np.array(distance), np.array(elevation)


def test_a_flat_route_has_no_climb():
    assert climbs.detect(*_route((5000, 0))) == []


def test_a_steady_climb_is_found_with_its_gain_and_grade():
    [climb] = climbs.detect(*_route((1000, 0), (1000, 8), (1000, -4)))
    assert climb.main
    assert climb.start_m == pytest.approx(1000, abs=80)
    assert climb.end_m == pytest.approx(2000, abs=80)
    assert climb.gain_m == pytest.approx(80, abs=5)
    assert climb.grade_pct == pytest.approx(8, abs=1)
    assert climb.sections[0].start_m == climb.start_m
    assert climb.sections[-1].end_m == climb.end_m


def test_a_real_descent_separates_two_climbs():
    found = climbs.detect(*_route((800, 0), (600, 10), (600, -8), (600, 10), (800, 0)))
    assert len(found) == 2
    assert found[0].end_m < found[1].start_m


def test_a_short_rise_below_the_thresholds_is_not_a_climb():
    assert climbs.detect(*_route((1000, 0), (60, 6), (1000, 0))) == []


def test_a_false_flat_stays_inside_the_climb():
    [climb] = climbs.detect(*_route((800, 0), (500, 9), (200, 0.5), (500, 9), (800, 0)))
    assert climb.gain_m == pytest.approx(91, abs=6)


def test_a_wall_then_a_gentle_slope_are_two_sections():
    [climb] = climbs.detect(*_route((800, 0), (400, 14), (600, 3), (800, -4)))
    grades = [section.grade_pct for section in climb.sections]
    steep = grades.index(max(grades))
    assert max(grades) > 12
    assert any(grade < 5 for grade in grades[steep + 1:])


def test_an_altimeter_spike_does_not_make_a_climb():
    distance, elevation = _route((3000, 0))
    elevation[300] += 40
    assert climbs.detect(distance, elevation) == []


def test_a_bump_is_found_but_not_main():
    found = climbs.detect(*_route((1000, 0), (250, 6), (1000, 0)))
    assert [climb.main for climb in found] == [False]


def test_irregular_samples_give_the_same_climb():
    distance, elevation = _route((1000, 0), (1000, 8), (1000, 0), step_m=1.0)
    rng = np.random.default_rng(7)
    keep = np.sort(np.concatenate([[0, distance.size - 1],
                                   rng.choice(distance.size, 600, replace=False)]))
    [dense] = climbs.detect(distance, elevation)
    [sparse] = climbs.detect(distance[np.unique(keep)], elevation[np.unique(keep)])
    assert sparse.gain_m == pytest.approx(dense.gain_m, abs=3)
    assert sparse.start_m == pytest.approx(dense.start_m, abs=30)


def test_a_perfectly_level_top_runs_the_summit_to_its_end():
    """The summit is the LAST highest point: a level plateau, which no terrain model gives,
    carries the end of the climb to its far side. The gain does not change."""
    [climb] = climbs.detect(*_route((1000, 0), (1000, 8), (600, 0), (1000, -4)))
    assert climb.end_m > 2400
    assert climb.gain_m == pytest.approx(80, abs=5)


def test_a_route_too_short_to_resample_has_no_climb():
    assert climbs.detect(np.array([0.0, 3.0]), np.array([100.0, 101.0])) == []
