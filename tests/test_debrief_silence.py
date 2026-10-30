"""A mute debrief is a failure to retry, not a message for the athlete.

Failure mode: the debrief runs and the coach searches honestly (Strava tool in its
list, repository grep, activity page, access token, curl), but has no access to
Strava because the data source is only declared for one engine. The log then says
"nothing useful to say" for an activity that has just arrived. That phrase makes the
failure invisible: a morning note may have nothing to say, a debrief may not -- it
exists ONLY because there is something to debrief.
"""

import time
from unittest.mock import MagicMock

from conftest import FakeAgent

from redline.service.agent import RunResult
from redline.service.proactive import ProactiveJob
from redline.service.worker import SILENCE_IS_A_FAILURE


def _silent_bot(make_bot):
    """A coach that chooses to stay silent (`send=false`)."""
    client = MagicMock()
    client.send_text.return_value = MagicMock(ok=True)
    return make_bot(agent=FakeAgent(result=RunResult(full_text="", silent=True)),
                    client=client), client


def _claimed(store, kind):
    store.proactive.enqueue_job(f"{kind}:1", kind, "90000000002", time.time() - 1)
    record = store.proactive.claim_due_job(time.time())
    return ProactiveJob(psid="4242", kind=kind, prompt="débriefe", job_id=record["id"],
                        detail=record["detail"], attempts=record["attempts"])


def test_a_debrief_that_goes_silent_is_retried_once_then_failed(make_bot, store):
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


def test_a_monthly_note_keeps_the_right_to_say_nothing(make_bot):
    """Silence remains the right answer when nothing deserves a message: turning every
        SILENCE into an incident would make the channel chatty, which is the other fault."""
    bot, client = _silent_bot(make_bot)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="monthly", prompt="magazine"))

    client.send_text.assert_not_called()


def test_a_morning_note_may_also_stay_silent(make_bot, store):
    bot, client = _silent_bot(make_bot)
    bot._repo.weekly_plan_message.return_value = ""
    bot._repo.weekly_plan_context.return_value = '{"revision": 2, "sessions": []}'

    bot._respond_proactive(_claimed(store, "morning"))

    client.send_text.assert_not_called()
    client.send_burst.assert_not_called()
    assert store.proactive.jobs()[0]["status"] == "done"


def test_only_the_activity_driven_triggers_treat_silence_as_a_failure():
    """The list is explicit: a trigger added tomorrow picks its side, it does not
        inherit it from a default."""
    assert {"strava_debrief", "strava_debrief_late"} == SILENCE_IS_A_FAILURE


def test_a_debrief_that_actually_speaks_is_never_flagged_as_an_incident(make_bot):
    client = MagicMock()
    client.send_text.return_value = MagicMock(ok=True)
    client.send_burst.return_value = MagicMock(ok=True)
    bot = make_bot(agent=FakeAgent(result=RunResult(full_text="Belle sortie, 6,9 km.")),
                   client=client)

    bot._respond_proactive(ProactiveJob(psid="4242", kind="strava_debrief",
                                        prompt="débriefe", detail="90000000002"))

    client.send_text.assert_not_called()
