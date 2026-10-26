"""L'adaptateur du banc, choisi par `AGENT_PROVIDER` comme en production.

Le banc ne parlait qu'à Codex ; la production est passée à Claude. Les deux adaptateurs
exposent `one_shot` avec le même contrat : un tour isolé, sans outil ni session.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from redline.agent import RunSpec
    from redline.config import Config


class OneShot(Protocol):
    def one_shot(self, message: str, *, spec: RunSpec, output_schema: dict | None = None,
                 instructions: str | None = None, label: str = "__one_shot__") -> str:
        ...

    def close(self) -> None:
        ...


def bench_runner(config: Config) -> OneShot:
    from redline.state import StateStore

    if config.agent_provider == "codex":
        from redline.codex import CodexRunner
        return CodexRunner(config, cast(StateStore, None))
    from redline.claude import ClaudeCodeRunner
    return ClaudeCodeRunner(config, cast(StateStore, None))


def check_model(config: Config, spec: RunSpec) -> None:
    """Le banc ne paie que les modèles des profils canoniques (`routing.py`) : une
    surcharge d'environnement vers un autre modèle échoue avant le premier appel."""
    from redline.routing import default_models

    allowed = default_models(config.agent_provider)
    if spec.model not in allowed:
        raise ValueError(f"Modèle non autorisé pour {config.agent_provider} : {spec.model}")
