"""The bench adapter, chosen by `AGENT_PROVIDER` as in production.

Both adapters expose `one_shot` with the same contract: an isolated turn, with no tool
and no session.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from redline.service.agent import RunSpec
    from redline.service.config import Config


class OneShot(Protocol):
    def one_shot(self, message: str, *, spec: RunSpec, output_schema: dict | None = None,
                 instructions: str | None = None, label: str = "__one_shot__") -> str:
        ...

    def close(self) -> None:
        ...


def bench_runner(config: Config) -> OneShot:
    from redline.service.state import StateStore

    if config.agent_provider == "codex":
        from redline.service.codex import CodexRunner
        return CodexRunner(config, cast(StateStore, None))
    from redline.service.claude import ClaudeCodeRunner
    return ClaudeCodeRunner(config, cast(StateStore, None))


def check_model(config: Config, spec: RunSpec) -> None:
    """The bench only pays for the models of the canonical profiles (`routing.py`): an
    environment override to another model fails before the first call."""
    from redline.service.routing import default_models

    allowed = default_models(config.agent_provider)
    if spec.model not in allowed:
        raise ValueError(f"model not allowed for {config.agent_provider}: {spec.model}")
