"""Composition du moteur de conversation sélectionné au démarrage."""

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
    # Config valide déjà ce champ ; cette garde protège les constructions directes.
    raise ValueError(f"fournisseur d'agent inconnu : {config.agent_provider!r}")
