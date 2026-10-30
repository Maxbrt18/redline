"""Composition of the conversation engine selected at startup."""

from .agent import AgentRunner
from .claude import ClaudeCodeRunner
from .codex import CodexRunner
from .config import Config
from .state import StateStore


def build_agent(config: Config, state: StateStore) -> AgentRunner:
    if config.agent_provider == "claude":
        return ClaudeCodeRunner(config, state)
    if config.agent_provider == "codex":
        return CodexRunner(config, state)
    # Config already validates this field; this guard protects direct constructions.
    raise ValueError(f"unknown agent provider: {config.agent_provider!r}")
