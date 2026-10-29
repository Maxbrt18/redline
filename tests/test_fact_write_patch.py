"""`--patch` writes exactly the fields it names and refuses everything else.

Without it, the upsert requires ALL fields, so changing `due_on` means resending the
whole long `item` text. The risk is not the date, which is re-read, but a long text
rewritten from memory and silently truncated.

These tests hold both ends. The merge really preserves what is not named, and the
payload stays COMPLETE (otherwise `command_key` and `apply_turn_with_receipts` change
contract unnoticed). Both refusals hold: `--patch` on a missing key, and a missing
required field without `--patch` -- that obligation no longer comes from argparse's
`required=True`, so the library does not guarantee it and it must be tested here.
"""

from __future__ import annotations

import datetime as dt

import pytest
from conftest import load_script

from redline.model.errors import InputError

fact_write = load_script("fact-write")


def test_question_review_cli_preserves_partial_and_deferred_states():
    args = parse("question", "--question-id", "q1", "--answer-id", "m1",
                 "--status", "deferred", "--note", "Reprise lundi.",
                 "--follow-up-on", "2026-09-07")
    _, kind, payload = fact_write._payload(args)
    assert kind == "question.review"
    assert payload["status"] == "deferred"
    assert payload["follow_up_on"] == dt.date(2026, 9, 7)


def test_ask_cli_names_only_the_measurement_actually_requested():
    args = parse("ask", "--text", "Quel était ton RPE ?", "--cell", "2026-09-05:easy:rpe")
    _, kind, payload = fact_write._payload(args)
    assert kind == "question.ask"
    assert payload == {"text": "Quel était ton RPE ?", "cells": ["2026-09-05:easy:rpe"]}


def parse(*argv: str):
    return fact_write.parser().parse_args(argv)


def deadline_row(**overrides) -> dict:
    row = {"deadline_key": "d-1", "due_on": dt.date(2026, 8, 11),
           "item": "texte long " * 200, "fact_owner": "`coaching/strength.md`",
           "blocks": "dose pliométrique de W34", "status": "todo",
           "completed_on": None}
    return {**row, **overrides}


def test_patch_touches_only_the_cited_field():
    existing = deadline_row()
    args = parse("deadline", "--key", "d-1", "--patch", "--due-on", "2026-08-23")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["due_on"] == dt.date(2026, 8, 23)
    assert payload["item"] == existing["item"]
    assert payload["fact_owner"] == existing["fact_owner"]
    assert payload["blocks"] == existing["blocks"]
    assert payload["status"] == "todo"


def test_the_payload_stays_complete_so_the_queue_keeps_its_contract():
    """A payload with holes would break `apply_turn_with_receipts` silently."""
    args = parse("deadline", "--key", "d-1", "--patch", "--due-on", "2026-08-23")
    _, kind, payload = fact_write._payload(args, deadline_row())
    assert kind == "deadline.upsert"
    assert set(payload) == {"deadline_key", "due_on", "item", "fact_owner",
                            "blocks", "status", "completed_on", "kind", "starts_on",
                            "ends_on", "context", "athlete_label"}


def test_patch_on_a_thread_keeps_its_body():
    existing = {"thread_key": "t-1", "opened_on": dt.date(2026, 8, 11),
                "kind": "promise", "body": "corps très long", "status": "open",
                "follow_up_on": dt.date(2026, 8, 12)}
    args = parse("thread", "--key", "t-1", "--patch", "--status", "closed")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["status"] == "closed"
    assert payload["body"] == "corps très long"
    assert payload["kind"] == "promise"
    assert payload["follow_up_on"] == dt.date(2026, 8, 12)


def test_without_patch_a_missing_required_field_is_refused_and_named():
    """The obligation lives outside argparse: if it is not tested, it disappears."""
    args = parse("deadline", "--key", "d-1", "--due-on", "2026-08-23")
    with pytest.raises(InputError) as refusal:
        fact_write._payload(args, None)
    message = str(refusal.value)
    assert "--item" in message and "--fact-owner" in message and "--status" in message
    assert "--due-on" not in message


def test_without_patch_the_full_form_writes_the_whole_row():
    args = parse("deadline", "--key", "d-1", "--due-on", "2026-08-23",
                 "--item", "neuf", "--fact-owner", "`coaching/strength.md`",
                 "--status", "todo")
    _, _, payload = fact_write._payload(args, None)
    assert payload["item"] == "neuf"
    assert payload["blocks"] is None and payload["completed_on"] is None


def test_patch_on_a_missing_key_refuses_instead_of_creating_holes():
    class Store:
        def deadline_rows(self):
            return [deadline_row(deadline_key="autre")]

    with pytest.raises(InputError, match="key that does not exist"):
        fact_write._existing(Store(), "deadline", "d-1")


def test_a_cited_empty_value_really_overwrites():
    """`--blocks ''` is a value, not an absence -- otherwise nothing could be emptied."""
    args = parse("deadline", "--key", "d-1", "--patch", "--blocks", "")
    _, _, payload = fact_write._payload(args, deadline_row())
    assert payload["blocks"] == ""


def test_exposure_carries_intent_separately_from_the_observed_cadence():
    args = parse(
        "exposure", "--key", "cadence:2026-09-02:ef",
        "--observation-key", "messenger:2026-09-02:ef",
        "--exposure-type", "cadence", "--origin", "voluntary",
        "--status", "confirmed", "--duration-min", "44",
        "--target-value", "172", "--target-unit", "spm", "--tolerance", "easy")

    entity, kind, payload = fact_write._payload(args)

    assert entity == "exposure:cadence:2026-09-02:ef"
    assert kind == "exposure.upsert"
    assert payload["origin"] == "voluntary"
    assert payload["target_value"] == 172


def test_the_notebook_label_survives_a_patch_and_an_empty_string_removes_it():
    """A deadline keeps its label when its date is shifted."""
    existing = deadline_row(athlete_label="Acheter la frontale")
    args = parse("deadline", "--key", "d-1", "--patch", "--due-on", "2026-08-23")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["athlete_label"] == "Acheter la frontale"
    args = parse("deadline", "--key", "d-1", "--patch", "--athlete-label", " ")
    _, _, payload = fact_write._payload(args, existing)
    assert payload["athlete_label"] is None


def test_a_correction_enters_the_database_only_once_written_to_the_journal(tmp_path, monkeypatch):
    # Corrections must also exist in the vault file, within reach of the owners check and
    # the replay registry.
    entry = "2026-09-17 — Une distance disponible sur Strava dite invérifiable"
    (tmp_path / "coaching").mkdir()
    (tmp_path / "coaching" / "corrections.md").write_text(f"## {entry}\n\nTexte.\n")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    args = parse("correction", "--correction-key", "k", "--summary", "s", "--entry", entry)
    _, kind, payload = fact_write._payload(args)
    assert kind == "correction.register"
    assert payload == {"correction_key": "k", "summary": "s"}
    with pytest.raises(InputError, match="is not an entry"):
        fact_write._payload(parse("correction", "--correction-key", "k", "--summary", "s",
                                  "--entry", "2026-09-17 — inventée"))


def test_a_settled_prediction_is_corrected_with_its_reason():
    _, _, payload = fact_write._payload(parse("prediction", "--key", "w40", "--rectify",
                                              "condition non tenue"))
    assert payload == {"prediction_key": "w40", "operation": "rectify",
                       "reason": "condition non tenue"}


def test_a_body_prediction_attaches_to_the_wake_reading_that_settles_it():
    base = ("prediction", "--key", "w42-sl", "--domain", "corps", "--statement", "trace",
            "--probability", "0.75", "--due-on", "2026-10-19")
    _, _, payload = fact_write._payload(parse(*base, "--resolves", "wake-trace", "--resolves-on",
                                              "2026-10-19", "--success-if", "<=1"))
    assert payload["resolves_on"] == {"kind": "wake-trace", "date": "2026-10-19",
                                      "success_if": "<=1"}
    assert "resolves_on" not in fact_write._payload(parse(*base))[2]
    with pytest.raises(InputError, match="go together"):
        fact_write._payload(parse(*base, "--resolves", "wake-trace"))


def test_a_prediction_reason_must_fit_its_outcome():
    payload = fact_write._prediction(
        parse("prediction", "--key", "k", "--outcome", "false", "--reason", "optimistic"), None)
    assert payload["reason"] == "optimistic"
    with pytest.raises(InputError, match="does not go with --outcome void"):
        fact_write._prediction(
            parse("prediction", "--key", "k", "--outcome", "void", "--reason", "cautious"), None)
    with pytest.raises(InputError, match="does not go with --outcome true"):
        fact_write._prediction(
            parse("prediction", "--key", "k", "--outcome", "true", "--reason", "scope"), None)
