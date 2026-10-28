"""Completing a measure must not require an identifier that nothing publishes.

The athlete gives the RPE and pain of a bike ride from the day before. The coach knows
the Strava id of the activity and tries `strava:90000000001`. But the row had been
recorded without an id, under a `messenger:` key, and that key appeared nowhere: not
in `fact-read.py observations`, nor elsewhere. It had only lived in the memory of the
session, long gone. Result: "unknown observation", turn refused, technical incident.
A check demanded a correct path that nothing made reachable.

Two remedies here: the key is readable (it is published), and the date is enough
(what the coach really knows). Plus a third, the most important: the error falls
IMMEDIATELY, while the coach can still retry, instead of waiting for the turn
boundary.
"""

import datetime as dt

import pytest

from redline.model.errors import InputError


def _cli():
    from redline.cli import ledger_complete
    return ledger_complete


class _Store:
    def __init__(self, rows):
        self._rows = rows

    def observation_rows(self):
        return self._rows

    def observation_id(self, key):
        return next((row["id"] for row in self._rows
                     if row["idempotency_key"] == key), None)


def _row(date, kind, key, identifier=1):
    return {"id": identifier, "occurred_on": dt.date.fromisoformat(date),
            "kind": kind, "idempotency_key": key}


VELO = _row("2026-08-13", "bike", "messenger:2026-08-13:velo-remplace-renfob")
REVEIL = _row("2026-08-13", "wake-trace", "messenger:abc:reveil", identifier=2)


def test_a_date_resolves_the_key_the_coach_cannot_guess():
    """The coach has the date, not a key invented the day before."""
    cli = _cli()
    assert cli.resolve_key(_Store([VELO]), "", "2026-08-13", "") == VELO["idempotency_key"]


def test_an_explicit_key_still_wins():
    cli = _cli()
    assert cli.resolve_key(_Store([VELO]), "clé-explicite", "2026-08-13", "") == "clé-explicite"


def test_an_ambiguous_date_lists_the_candidates_with_their_keys():
    """Two rows on the same day: we do not guess in the coach's place, we show it the
        keys so it can decide. An error that does not expose the way out forces a second
        guess."""
    cli = _cli()
    with pytest.raises(InputError) as refus:
        cli.resolve_key(_Store([VELO, REVEIL]), "", "2026-08-13", "")
    message = str(refus.value)
    assert "bike" in message and "wake-trace" in message
    assert VELO["idempotency_key"] in message
    assert "--type" in message


def test_a_type_disambiguates_without_needing_the_key():
    cli = _cli()
    assert cli.resolve_key(_Store([VELO, REVEIL]), "", "2026-08-13",
                           "wake-trace") == REVEIL["idempotency_key"]


def test_a_date_without_any_observation_names_where_to_look():
    cli = _cli()
    with pytest.raises(InputError, match=r"fact-read\.py observations"):
        cli.resolve_key(_Store([VELO]), "", "2026-08-01", "")


def test_the_key_is_published_by_the_reader_that_the_coach_runs():
    """The root remedy: `ledger-complete.py` requires a key, so the reader must publish
        it. Not displaying it amounted to asking for a secret identifier."""
    from redline.cli import fact_read

    rows = [{**VELO, "measurements": {"rpe": {"state": "pending"},
                                      "pain": {"state": "pending"}},
             "note": "vélo avec le padre"}]
    lignes = fact_read.observations(_Store(rows), 10)

    assert any(VELO["idempotency_key"] in ligne for ligne in lignes)
    # And what remains to be completed, otherwise the coach does not know what to ask for.
    assert any("à compléter : pain, rpe" in ligne for ligne in lignes)
