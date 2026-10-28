"""The two roots: the ENGINE (this repository) and the VAULT (an instance's notes).

They are distinct so the engine can be installed somewhere other than the vault it
serves; note locations are never derived from the code's own file location.

The rule:
- code, templates and fixtures are found from `ENGINE_ROOT`;
- everything an instance writes or reads as a note is found from `vault_root()`.

`VAULT_PATH` is the variable the service already reads (`redline.service.config`): one
variable for both, so a script launched by the coach and the service see the same
vault. When unset, the vault is the engine repository itself, so a single-folder
deployment works without configuration.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The checkout: an editable install keeps the package inside it.
ENGINE_ROOT = Path(__file__).resolve().parents[3]


def state_dir() -> Path:
    """Where a running service keeps what it writes outside PostgreSQL: the outbox of
    cards, the attachments being read, the Strava token cache. `REDLINE_STATE_DIR`, by
    default `var/` in the checkout; read at the call, like the vault."""
    raw = os.environ.get("REDLINE_STATE_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else ENGINE_ROOT / "var"


def outbox_dir() -> Path:
    """A PNG written here during a turn goes out to the athlete after the text."""
    return state_dir() / "outbox"


def vault_root() -> Path:
    """The instance's vault, read at call time (the service loads its .env after import)."""
    raw = os.environ.get("VAULT_PATH", "").strip()
    return Path(raw).expanduser().resolve() if raw else ENGINE_ROOT
