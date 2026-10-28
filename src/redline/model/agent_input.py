"""The JSON a coach turn hands to a CLI: a named file, never a redirection.

An input redirection (`... < /tmp/x.json`) reads a file, and a file outside the working
directory is not covered by the `Bash(/usr/local/bin/redline-agent-cli *)` permission;
whether it is allowed depends on how the agent binary parses the shell.

Hence two choices. The command stays a bare command (`--input <path>`), with no `<`, `|`
or `;`: the form least exposed to changes in that parsing. And the file lives in the
working directory, under a name specific to the turn: simultaneous turns do not
overwrite each other, and a failed `Write` never makes a turn reread another turn's JSON.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from redline.model import paths
from redline.model.errors import InputError

#: Ignored by Git (`.gitignore`): the service runs `git add -A` at the end of a turn.
DIRECTORY = ".agent-tmp"

_SUFFIX = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class AgentInputError(InputError):
    """The named file is not the one this turn is allowed to hand over."""


def turn_path(turn_id: str, suffix: str = "") -> str:
    """The vault-relative path the service announces to the turn."""
    if suffix and not _SUFFIX.match(suffix):
        raise AgentInputError(f"suffixe de fichier invalide : {suffix!r}")
    return f"{DIRECTORY}/{turn_id}{'-' + suffix if suffix else ''}.json"


def resolve(path: str, *, root: Path | None = None, turn_id: str | None = None) -> Path:
    """The file to read, refused if it does not belong to the current turn.

    Outside a turn (operator launches, tests), any readable path is fine: the operator
    knows what is passed. Inside a turn, only `.agent-tmp/<turn_id>[-<suffix>].json`.
    """
    root = root or paths.vault_root()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    turn_id = (os.environ.get("REDLINE_TURN_ID") or "").strip() if turn_id is None else turn_id
    if not turn_id:
        return candidate
    name = candidate.name
    stem = name[:-len(".json")] if name.endswith(".json") else ""
    suffix = stem[len(turn_id) + 1:] if stem.startswith(f"{turn_id}-") else None
    if (candidate.parent != (root / DIRECTORY).resolve()
            or not (stem == turn_id or (suffix and _SUFFIX.match(suffix)))):
        # The refusal says how to succeed: `cp` may be denied by the permission mode,
        # while `Write` is always allowed and is the only gesture to make.
        raise AgentInputError(
            f"--input : ce tour ne lit que {turn_path(turn_id)} "
            f"(ou {DIRECTORY}/{turn_id}-<nom>.json), reçu {path}. Réécris le même JSON "
            f"avec Write dans {turn_path(turn_id)}, puis relance la même commande avec "
            f"--input {turn_path(turn_id)}")
    return candidate


def read_text(path: str | None) -> str:
    """The content handed over: the `--input` file, or stdin outside a turn."""
    if path is None:
        return sys.stdin.read()
    target = resolve(path)
    try:
        return target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AgentInputError(f"--input : fichier absent ({path}) ; écris-le avec Write "
                              "avant d'appeler la CLI") from None


def read_json(path: str | None) -> Any:
    """The JSON handed over; `json.JSONDecodeError` if unreadable."""
    return json.loads(read_text(path))


def clear(turn_id: str, *, root: Path | None = None) -> None:
    """Delete a turn's files, whatever its outcome."""
    directory = (root or paths.vault_root()) / DIRECTORY
    if not turn_id or not directory.is_dir():
        return
    for path in directory.glob(f"{turn_id}*.json"):
        path.unlink(missing_ok=True)
