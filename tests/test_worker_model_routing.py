"""TurnWorker routing: which proactive kinds run heavy and isolated vs light.
"""

import pytest
from conftest import FakeAgent

from redline.service.agent import RunResult, SessionMode
from redline.service.proactive import ProactiveJob


def _bot(make_bot):
    agent = FakeAgent(result=RunResult(full_text="", silent=True))
    return make_bot(agent=agent), agent


@pytest.mark.parametrize("kind", ["weekly", "monthly"])
def test_heavy_kinds_run_sol_isolated(make_bot, kind):
    bot, agent = _bot(make_bot)
    bot._respond_proactive(ProactiveJob(psid="4242", kind=kind, prompt="p"))
    assert [(c["model"], c["session_mode"]) for c in agent.calls] == [
        ("gpt-6-sol", SessionMode.ISOLATED),
    ]


@pytest.mark.parametrize("kind", ["morning", "strava_debrief"])
def test_light_kinds_run_on_a_fork_of_the_conversation(make_bot, kind):
    """The light proactive turn sees the conversation without ever joining it."""
    bot, agent = _bot(make_bot)
    bot._respond_proactive(ProactiveJob(psid="4242", kind=kind, prompt="p"))
    assert [(c["model"], c["session_mode"]) for c in agent.calls] == [
        ("gpt-6-luna", SessionMode.FORK),
    ]
    assert agent.calls[0]["spec"].structured_reply


def test_heavy_kinds_get_their_own_effort_and_timeout(make_bot):
    """Weekly review does not share the interactive budget.

    The expensive run needs a longer timeout than a text message, and its failure must
    not consume the proactive budget of the next day. This test pins BOTH halves: if
    either falls back to the interactive setting, the failure mode returns silently.
    """
    bot, agent = _bot(make_bot)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="weekly", prompt="p"))
    assert [(c["effort"], c["timeout"]) for c in agent.calls] == [("high", 5400)]


@pytest.mark.parametrize("kind", ["morning", "strava_debrief"])
def test_light_kinds_keep_the_interactive_budget(make_bot, kind):
    bot, agent = _bot(make_bot)
    bot._respond_proactive(ProactiveJob(psid="4242", kind=kind, prompt="p"))
    assert [(c["effort"], c["timeout"]) for c in agent.calls] == [("high", 600)]
