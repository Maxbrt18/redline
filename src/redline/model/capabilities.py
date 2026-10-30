"""Analysis notes of the running capabilities.

Their frontmatter carries no operational state: scores, statuses, dates and next tests
live exclusively in PostgreSQL. The notes keep only their Obsidian identity and the
human reasoning of their body.
"""

from __future__ import annotations

from pathlib import Path

from . import vault_layout
from .notes import read_all
from .schema import Schema, field

CAPABILITY_SCHEMA = Schema("capability note", [
    field("type", str, choices=("capability",)),
    field("capability_number", int, minimum=1, maximum=9),
    field("aliases", list, required=False, item=field("alias", str)),
    field("tags", list, required=False, item=field("tag", str)),
])


def load(vault: Path) -> dict:
    """Load the current analysis notes, without operational state."""
    return read_all(vault / vault_layout.CAPABILITIES, CAPABILITY_SCHEMA)
