"""A mute debrief: a decision when the activity was read, a failure to retry when not.

Failure mode: the debrief runs and the coach searches honestly (Strava tool in its
list, repository grep, activity page, access token, curl), but has no access to
Strava because the data source is only declared for one engine. The log then says
"nothing useful to say" for an activity that has just arrived. That phrase makes the
failure invisible.

The other fault: the prompt lets a debrief stay silent as a decision, not a failure,
and a debrief handled with nothing worth sending was retried as if unread. What tells
them apart is a fact the service checks, not the coach's word: the activity recorded
in the ledger.
"""

import time
from unittest.mock import MagicMock

import pytest
from conftest import FakeAgent

from redline.service.agent import RunResult, structured_reply
from redline.service.proactive import ProactiveJob


def _silent_bot(make_bot, *, recorded=False, silent=True):
    """A coach that stays silent (`send=false`, or no text at all when not `silent`),
    on an activity the ledger holds or not."""
    client = MagicMock()
    client.send_text.return_value = MagicMock(ok=True)
    bot = make_bot(agent=FakeAgent(result=RunResult(full_text="", silent=silent)),
                   client=client)
    bot._repo.activity_recorded.return_value = recorded
    return bot, client


def _claimed(store, kind):
    store.proactive.enqueue_job(f"{kind}:1", kind, "90000000002", time.time() - 1)
    record = store.proactive.claim_due_job(time.time())
    return ProactiveJob(psid="4242", kind=kind, prompt="débriefe", job_id=record["id"],
                        detail=record["detail"], attempts=record["attempts"])


def test_a_debrief_silent_on_an_unrecorded_activity_is_retried_once_then_failed(
        make_bot, store):
    """A single retry, instead of every 5 minutes for four hours."""
    bot, client = _silent_bot(make_bot)

    bot._respond_proactive(_claimed(store, "strava_debrief"))
    assert store.proactive.jobs(active_only=True)[0]["kind"] == "strava_debrief"
    store.proactive.retry_job("strava_debrief:1", time.time() - 1, time.time() + 3600)
    bot._respond_proactive(_claimed(store, "strava_debrief"))

    client.send_text.assert_not_called()
    job = store.proactive.jobs()[0]
    assert job["status"] == "failed" and "silent" in job["last_error"]
    assert len(bot._agent.calls) == 2


def test_the_deferred_debrief_is_held_to_the_same_rule(make_bot, store):
    bot, client = _silent_bot(make_bot)

    bot._respond_proactive(_claimed(store, "strava_debrief_late"))

    assert not client.send_text.called
    assert store.proactive.jobs(active_only=True)[0]["kind"] == \
        "strava_debrief_late"


def test_a_debrief_silent_on_a_recorded_activity_is_handled_without_a_message(
        make_bot, store):
    bot, client = _silent_bot(make_bot, recorded=True)

    bot._respond_proactive(_claimed(store, "strava_debrief"))

    client.send_text.assert_not_called()
    client.send_burst.assert_not_called()
    assert store.proactive.jobs()[0]["status"] == "done"
    assert len(bot._agent.calls) == 1, "a decision is not retried"


@pytest.mark.parametrize("recorded", [None, False])
def test_an_inconclusive_or_missing_record_keeps_the_retry(make_bot, store, recorded):
    bot, _ = _silent_bot(make_bot, recorded=recorded)

    bot._respond_proactive(_claimed(store, "strava_debrief"))

    assert store.proactive.jobs(active_only=True)[0]["kind"] == "strava_debrief"


def test_a_debrief_with_no_text_and_no_typed_silence_is_unread(make_bot, store):
    bot, _ = _silent_bot(make_bot, recorded=True, silent=False)

    bot._respond_proactive(_claimed(store, "strava_debrief"))
    store.proactive.retry_job("strava_debrief:1", time.time() - 1, time.time() + 3600)
    bot._respond_proactive(_claimed(store, "strava_debrief"))

    job = store.proactive.jobs()[0]
    assert job["status"] == "failed" and "no text" in job["last_error"]


def test_a_monthly_note_keeps_the_right_to_say_nothing(make_bot):
    """Silence remains the right answer when nothing deserves a message: turning every
        SILENCE into an incident would make the channel chatty, which is the other fault."""
    bot, client = _silent_bot(make_bot)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="monthly", prompt="magazine"))

    client.send_burst.assert_not_called()
    assert len(bot._agent.calls) == 1, "a silent monthly note is not retried"


def test_a_morning_note_may_also_stay_silent(make_bot, store):
    bot, client = _silent_bot(make_bot)
    bot._repo.weekly_plan_message.return_value = ""
    bot._repo.active_revision.return_value = {"revision": 2, "sessions": []}

    bot._respond_proactive(_claimed(store, "morning"))

    client.send_text.assert_not_called()
    client.send_burst.assert_not_called()
    assert store.proactive.jobs()[0]["status"] == "done"



def test_a_debrief_that_actually_speaks_is_never_flagged_as_an_incident(make_bot, caplog):
    client = MagicMock()
    client.send_text.return_value = MagicMock(ok=True)
    client.send_burst.return_value = MagicMock(ok=True)
    bot = make_bot(agent=FakeAgent(result=RunResult(full_text="Belle sortie, 6,9 km.")),
                   client=client)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail="90000000002"))

    assert "Belle sortie" in client.send_burst.call_args.args[1]
    assert "silent" not in caplog.text


@pytest.mark.parametrize(("raw", "expected"), [
    ({"send": False, "text": ""}, (False, "")),
    ({"send": False, "text": "pensée"}, (False, "")),
    ({"send": True, "text": " Belle sortie. "}, (True, "Belle sortie.")),
])
def test_the_typed_reply_says_whether_a_message_goes(raw, expected):
    assert structured_reply(raw) == expected


def test_asking_to_send_an_empty_text_is_not_a_typed_silence():
    with pytest.raises(ValueError, match="empty text"):
        structured_reply({"send": True, "text": "  "})
