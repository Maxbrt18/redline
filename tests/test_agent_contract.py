"""Contract shared by the conversation engines.

The bot must know neither the Claude Code flags nor the Codex SDK objects. It only
expresses the lifecycle expected for the session.
"""

from redline.service.agent import RunResult, Triage
from redline.service.claude import ClaudeCodeRunner
from redline.service.codex import CodexRunner


def test_the_common_result_types_keep_the_existing_safe_defaults():
    assert RunResult().full_text == ""
    assert RunResult().error is None
    assert Triage().depth == "N1"
    assert Triage().emotional is False


def test_the_composition_factory_selects_only_the_configured_provider(make_config, store):
    from redline.service.agent_factory import build_agent

    assert isinstance(build_agent(make_config(agent_provider="claude"), store), ClaudeCodeRunner)
    assert isinstance(build_agent(make_config(agent_provider="codex"), store), CodexRunner)
