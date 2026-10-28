"""Contrat d'entrée et complétude des observations typées."""

import datetime as dt

from redline.analysis import ledger
from redline.model import ledger as table

PENDING = {"state": "pending"}


def typed_observation(day: dt.date, kind="easy", *, strava=123, pains=None,
                      **measurements) -> dict:
    """La douleur de la zone fictive naît `pending`, comme le repository la crée."""
    return {"occurred_on": day, "kind": kind, "strava_activity_id": strava,
            "measurements": measurements,
            "pains": {"injury": PENDING} if pains is None else pains}


def test_every_session_type_is_classified():
    explicitly_metric_free = {"instruments", "palpation"}
    unclassified = {
        kind for kind in table.SESSION_TYPES
        if kind not in table.EFFORT_TYPES and kind not in table.REST_TYPES
    } - explicitly_metric_free
    assert not unclassified, f"types sans famille : {sorted(unclassified)}"


def test_input_requires_a_date_and_a_known_type():
    assert table.INPUT_SCHEMA.errors({"Type": "easy"}, "observation")
    problems = table.INPUT_SCHEMA.errors(
        {"Date": "2026-08-20", "Type": "footing"}, "observation")
    assert any("Type" in problem for problem in problems)


def test_only_declarative_inputs_may_be_unavailable():
    base = {"Date": "2026-08-20", "Type": "easy"}
    assert table.INPUT_SCHEMA.errors({**base, "km": "?"}, "observation")
    assert table.INPUT_SCHEMA.errors(
        {**base, "RPE": "?", "Plaisir": "?"}, "observation") == []
    assert table.pain_reading("?") == {"state": "unavailable"}


def test_notes_accept_pipes_because_the_markdown_table_is_gone():
    payload = {"Date": "2026-08-20", "Type": "easy", "Note": "EF | très souple"}
    assert table.INPUT_SCHEMA.errors(payload, "observation") == []


def test_typed_completeness_reads_metric_states():
    observation = typed_observation(
        dt.date(2026, 8, 4),
        distance_km={"state": "known", "value": 7.0},
        rpe={"state": "pending"},
    )
    assert table.missing_observation_fields(observation) == [
        "RPE", "Plaisir", "Dlr:injury", "D+", "D-"]


def test_each_watched_zone_is_claimed_under_its_own_name():
    observation = typed_observation(
        dt.date(2026, 8, 4), strava=None, rpe={"state": "known", "value": 3},
        enjoyment={"state": "known", "value": 6},
        pains={"genou": PENDING, "cheville": {"state": "known", "value": 1}})
    assert table.missing_observation_fields(observation) == ["Dlr:genou"]


def test_without_a_watched_zone_no_pain_is_claimed():
    observation = typed_observation(dt.date(2026, 8, 4), strava=None, pains={})
    assert table.missing_observation_fields(observation) == ["RPE", "Plaisir"]


def test_unavailable_only_resolves_declarative_metrics():
    unavailable = {"state": "unavailable"}
    observation = typed_observation(
        dt.date(2026, 8, 4), rpe=unavailable, pains={"injury": unavailable},
        enjoyment=unavailable, elevation_gain_m=unavailable,
        elevation_loss_m=unavailable,
    )
    assert table.missing_observation_fields(observation) == ["D+", "D-"]


def test_readings_require_pain_but_palpation_requires_no_number():
    reading = typed_observation(dt.date(2026, 8, 4), kind="wake-trace", strava=None)
    palpation = typed_observation(dt.date(2026, 8, 4), kind="palpation", strava=None)
    assert table.missing_observation_fields(reading) == ["Dlr:injury"]
    assert table.missing_observation_fields(palpation) == []


def test_pending_and_lapsed_windows_do_not_overlap():
    observation = typed_observation(dt.date(2026, 8, 4))
    assert table.pending_observation_fields([observation], dt.date(2026, 8, 5))
    assert table.lapsed_observation_fields([observation], dt.date(2026, 8, 5)) == []
    assert table.pending_observation_fields([observation], dt.date(2026, 8, 6)) == []
    assert table.lapsed_observation_fields([observation], dt.date(2026, 8, 6))


def test_completeness_does_not_rewrite_history():
    old = typed_observation(table.COMPLETENESS_FROM - dt.timedelta(days=1))
    assert table.observation_completeness_errors([old], dt.date(2026, 8, 20)) == []


def test_unavailable_is_never_read_as_a_value():
    unavailable = {"measurements": {"rpe": {"state": "unavailable", "value": None}}}
    known = {"measurements": {"rpe": {"state": "known", "value": 1.5}}}
    assert ledger._known(unavailable, "rpe") is None
    assert ledger._known(known, "rpe") == 1.5


def test_cell_key_is_stable_and_metric_specific():
    entry = {"date": "2026-08-04", "type": "strength", "missing": ["RPE", "Dlr"]}
    assert table.cell_key(entry, "RPE") == "2026-08-04:strength:RPE"
    assert table.cell_key(entry, "RPE") != table.cell_key(entry, "Dlr")
