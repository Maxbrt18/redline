"""Writing facts the way every command does: through `OperationalStore.submit`."""

from __future__ import annotations

from typing import Any

from redline.model import commands


def write(store: Any, kind: str, payload: dict, *, actor: str = "test") -> Any:
    """Apply one command outside a turn and return what its write returned."""
    return store.submit(kind, payload, command_key=f"test:{commands.digest(payload)}",
                        turn_id="", actor=actor).result
