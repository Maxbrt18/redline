"""CoachBot routing: which proactive kinds run heavy and isolated vs light.
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
    """Le proactif léger voit la conversation sans jamais s'y inscrire."""
    bot, agent = _bot(make_bot)
    bot._respond_proactive(ProactiveJob(psid="4242", kind=kind, prompt="p"))
    assert [(c["model"], c["session_mode"]) for c in agent.calls] == [
        ("gpt-6-luna", SessionMode.FORK),
    ]
    assert agent.calls[0]["spec"].structured_reply


def test_heavy_kinds_get_their_own_effort_and_timeout(make_bot):
    """Le bilan hebdo ne partage pas le budget de l'interactif.

    Le 2026-08-02, il a été tué à 600 s après avoir tout consommé, puis le budget
    proactif a bloqué le lendemain : le run coûteux avait le délai d'un texto. Ce
    test pince les DEUX moitiés du correctif -- si l'une repasse au réglage
    interactif, le même soir se reproduit sans rien casser d'autre.
    """
    bot, agent = _bot(make_bot)
    bot._respond_proactive(ProactiveJob(psid="4242", kind="weekly", prompt="p"))
    assert [(c["effort"], c["timeout"]) for c in agent.calls] == [("high", 5400)]


@pytest.mark.parametrize("kind", ["morning", "strava_debrief"])
def test_light_kinds_keep_the_interactive_budget(make_bot, kind):
    bot, agent = _bot(make_bot)
    bot._respond_proactive(ProactiveJob(psid="4242", kind=kind, prompt="p"))
    assert [(c["effort"], c["timeout"]) for c in agent.calls] == [("high", 600)]
