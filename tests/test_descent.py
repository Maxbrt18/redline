"""scripts/descent.py — le D− que l'API Strava ne renvoie pas.

Le contrôle qui compte est le dernier : sur la seule activité du registre dont le D−
était connu par ailleurs, la méthode retombe à 0,5 % près. Sans lui, ces tests ne
vérifieraient qu'une soustraction.
"""

import pytest

from redline.analysis import descent


def test_a_loop_descends_what_it_climbed():
    assert descent.descent(175.3, [262.9, 300.0, 262.6]) == 176


def test_a_pure_climb_barely_descends():
    """Lac-A J1 : montée à pied de 1108 m, arrivée en haut."""
    assert descent.descent(1108.1, [636.7, 1728.2]) == 17


def test_a_pure_descent_is_not_read_from_the_gain():
    """Lac-A J3 : 19,7 m de D+ pour ~1100 de D−. C'est exactement la ligne que la
    colonne D+ seule décrivait comme une journée plate."""
    assert descent.descent(19.7, [1683.4, 602.4]) == 1101


def test_the_result_is_never_negative():
    assert descent.descent(0.0, [100.0, 140.0]) == 0


def test_a_stream_of_one_point_is_refused_not_guessed():
    with pytest.raises(ValueError, match="deux points"):
        descent.descent(50.0, [512.0])


def test_the_mcp_payload_is_accepted_as_is():
    """Le flux passe tel quel depuis get_activity_streams, sans réemballage."""
    assert descent._altitude('{"altitude": [1, 2, 3]}') == [1, 2, 3]
    assert descent._altitude("[1, 2, 3]") == [1, 2, 3]


def test_a_payload_without_altitude_is_named():
    with pytest.raises(ValueError, match="altitude"):
        descent._altitude('{"heart_rate": [140, 150]}')


def test_matches_the_only_descent_strava_ever_gave_us():
    """2026-07-21, descente du Gotzenalm : 1096 m relevés dans Strava, seule ligne du
    registre à porter un D− avant le backfill. La méthode rend 1101."""
    computed = descent.descent(19.7, [1683.4, 602.4])
    assert abs(computed - 1096) / 1096 < 0.01
