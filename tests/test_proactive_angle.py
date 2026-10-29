"""The angle of a proactive message has a field -- so nothing is left to do in the prose.

A debrief once started by reciting the coach's machinery aloud ("angle not repeated
so far ..."). Hardening `STYLE_PROMPT` cannot hold: the trigger required "an angle you
have not already served", `fact-read proactive` was supposed to list them, and
**nothing ever wrote them** -- `record_proactive_delivery` hard-coded `angle: None`. The
model had to reconstruct the served angles from raw summaries and, having no field to
put that reasoning in, put it in the message.
General rule applied as is: **a guardrail that opens no correct path breeds its own
workaround.** The correct path is opened here.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import load_script

from redline.model import commands
from redline.model.errors import InputError
from redline.service.prompts import PROACTIVE_TRIGGERS, build_proactive_prompt
from redline.service.vault import VaultRepo

fact_write = load_script("fact-write")


def _job():
    return SimpleNamespace(kind="strava_debrief", job_id="job-1", detail="90000000010")


def test_the_delivered_trace_carries_the_angle_the_turn_deposited():
    store = MagicMock()
    store.turn_angle.return_value = "titre Strava, récit libre"
    repo = VaultRepo("/tmp/vault", "origin", "main", operational_store=store)

    repo.record_proactive_delivery(_job(), datetime.now().astimezone(), "Alors, « La douche »…",
                                   "turn-42")

    store.turn_angle.assert_called_once_with("turn-42")
    assert store.add_proactive_message.call_args.args[0]["angle"] == "titre Strava, récit libre"


def test_a_message_without_an_angle_records_none_rather_than_an_empty_string():
    """`angle` is nullable: «no angle» reads NULL, never an empty string."""
    store = MagicMock()
    store.turn_angle.return_value = ""
    repo = VaultRepo("/tmp/vault", "origin", "main", operational_store=store)

    repo.record_proactive_delivery(_job(), datetime.now().astimezone(), "texte", "turn-42")

    assert store.add_proactive_message.call_args.args[0]["angle"] is None


def test_the_cli_refuses_an_angle_outside_a_turn(monkeypatch, capsys):
    """Outside a turn, there is no proactive message to qualify."""
    monkeypatch.delenv("REDLINE_TURN_ID", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/y")
    monkeypatch.setattr("sys.argv", ["fact-write", "angle", "titre Strava"])

    with pytest.raises(InputError) as refusal:
        fact_write.main()

    assert "turn" in str(refusal.value)


def test_the_angle_is_a_turn_command_so_a_refused_turn_takes_it_along():
    assert commands.PROACTIVE_ANGLE in commands.ACTIVE


def test_both_paths_that_ask_for_an_angle_also_name_where_it_goes():
    """The instruction and its output travel together, otherwise the output does not
        exist. `build_proactive_prompt` wraps ALL the triggers: it carries the general
        rule, and the debrief -- the only one that explicitly chooses an angle -- repeats
        it at the exact place where the choice is made.

    """
    envelope = build_proactive_prompt("strava_debrief", "90000000010")
    assert "fact-write angle" in envelope
    assert "fact-write angle" in PROACTIVE_TRIGGERS["strava_debrief"]
