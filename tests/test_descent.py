"""scripts/descent.py -- the elevation loss that the Strava API does not return.

The check that matters is the last one: on the only ledger activity whose loss was
known otherwise, the method falls back within 0.5 %. Without it, these tests would
only verify a subtraction.
"""

import pytest

from redline.analysis import descent


def test_a_loop_descends_what_it_climbed():
    assert descent.descent(175.3, [262.9, 300.0, 262.6]) == 176


def test_a_pure_climb_barely_descends():
    """Day 1: 1108 m climbed on foot, arrival at the top."""
    assert descent.descent(1108.1, [636.7, 1728.2]) == 17


def test_a_pure_descent_is_not_read_from_the_gain():
    """Day 3: 19.7 m of gain for ~1100 of loss. This is exactly the row that the gain
        column alone described as a flat day."""
    assert descent.descent(19.7, [1683.4, 602.4]) == 1101


def test_the_result_is_never_negative():
    assert descent.descent(0.0, [100.0, 140.0]) == 0


def test_a_stream_of_one_point_is_refused_not_guessed():
    with pytest.raises(ValueError, match="deux points"):
        descent.descent(50.0, [512.0])


def test_the_mcp_payload_is_accepted_as_is():
    """The stream passes as is from get_activity_streams, without repackaging."""
    assert descent._altitude('{"altitude": [1, 2, 3]}') == [1, 2, 3]
    assert descent._altitude("[1, 2, 3]") == [1, 2, 3]


def test_a_payload_without_altitude_is_named():
    with pytest.raises(ValueError, match="altitude"):
        descent._altitude('{"heart_rate": [140, 150]}')


def test_matches_the_only_descent_strava_ever_gave_us():
    """Descent of a mountain: 1096 m recorded in Strava, the only ledger row to carry
        a loss before the backfill. The method returns 1101."""
    computed = descent.descent(19.7, [1683.4, 602.4])
    assert abs(computed - 1096) / 1096 < 0.01
