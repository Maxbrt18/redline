"""Contrat commun aux moteurs de conversation.

Le bot ne doit connaître ni les drapeaux de Claude Code, ni les objets du SDK
Codex. Il exprime seulement le cycle de vie attendu pour la session.
"""

from redline.service.agent import RunResult, RunSpec, SessionMode, Triage
from redline.service.claude import ClaudeCodeRunner
from redline.service.codex import CodexRunner


def test_the_common_result_types_keep_the_existing_safe_defaults():
    assert RunResult().full_text == ""
    assert RunResult().error is None
    assert Triage().depth == "N1"
    assert Triage().emotional is False


def test_session_modes_name_the_three_behaviours_the_bot_needs():
    assert {mode.value for mode in SessionMode} == {"continue", "isolated", "fork"}


def test_run_spec_is_the_common_provider_boundary():
    spec = RunSpec("sonnet", "low", 600)
    assert spec.model == "sonnet"
    assert spec.session_mode is SessionMode.CONTINUE
    assert spec.read_only is False


def test_the_composition_factory_selects_only_the_configured_provider(make_config, store):
    from redline.service.agent_factory import build_agent

    assert isinstance(build_agent(make_config(agent_provider="claude"), store), ClaudeCodeRunner)
    assert isinstance(build_agent(make_config(agent_provider="codex"), store), CodexRunner)
