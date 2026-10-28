"""A debrief that speaks without recording its session is an internal failure.

The counterpart of `required_weeks`. A prescriptive turn must PROVE that it revised
the week, otherwise nothing is sent. Nothing required a debrief to prove it had
recorded the session it had just talked about: its success criterion was "a message
was sent". Two failure modes this covers:
  - a single exec killed by the sandbox, the question asked about an activity that was
    never read nor recorded, yet logged as a SUCCESS;
  - the Strava script running without credentials, which ends in SILENCE.
Either way a session is lost and the weekly volume and gauge come out wrong. The mute
debrief was already treated as a failure (`test_debrief_silence.py`); the TALKATIVE
debrief that writes nothing was not.
"""

from datetime import time as dtime
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.proactive import ProactiveJob
from redline.service.vault import PersistenceResult

ACTIVITE = "90000000001"


@pytest.fixture
def client():
    client = MagicMock()
    for method in ("send_text", "send_burst", "send_image", "send_button", "action"):
        getattr(client, method).return_value = MagicMock(ok=True)
    return client


def _bot(make_bot, client, *, recorded: bool):
    repo = MagicMock()
    repo.ledger_cells.return_value = ([], [])
    repo.sync.return_value = False
    repo.ensure_clean.return_value = []
    repo.head.return_value = "0123456789abcdef"
    repo.weekly_plan_context.return_value = ""
    repo.weekly_plan_message.return_value = "Aujourd'hui : EF, plafond 9 km."
    repo.persist_turn.return_value = PersistenceResult(True)
    repo.drain_alerts.return_value = []
    repo.activity_recorded.return_value = recorded
    return make_bot(repo=repo, client=client,
                    # Window open for 24 h, as everywhere else in the suite: without it the test
                    # depends on the time of day. Run late in the evening, the three assertions
                    # fell on «budget/hour blocks the send» and the debrief contract was no
                    # longer verified at all.
                    proactive_active_start=dtime(0, 0),
                    agent=FakeAgent(result=RunResult(
                        full_text="Comment ont réagi les jambes sur ce vélo ?")))


def test_a_debrief_that_speaks_without_recording_is_logged_not_announced(
        make_bot, client, caplog):
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail=ACTIVITE))

    assert client.send_burst.called, "la prose du coach part quand même"
    client.send_text.assert_not_called()
    assert "debrief-non-consigne" in caplog.text


def test_the_check_asks_about_the_activity_of_this_very_job(make_bot, client):
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail=ACTIVITE))

    bot._repo.activity_recorded.assert_called_with(ACTIVITE)


def test_a_recorded_activity_raises_nothing(make_bot, client):
    """The negative control: a debrief that does its job produces no noise."""
    bot = _bot(make_bot, client, recorded=True)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail=ACTIVITE))

    client.send_text.assert_not_called()


def test_the_deferred_debrief_carries_the_same_contract(make_bot, client, caplog):
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief_late",
                                        prompt="débriefe", detail=ACTIVITE))

    client.send_text.assert_not_called()
    assert "debrief-non-consigne" in caplog.text


def test_a_trigger_without_an_activity_is_never_accused(make_bot, client):
    """The morning note starts from no activity: asking it to record one would
        invent a failure at every wake-up."""
    bot = _bot(make_bot, client, recorded=False)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="morning", prompt="matin"))

    client.send_text.assert_not_called()
    bot._repo.activity_recorded.assert_not_called()
