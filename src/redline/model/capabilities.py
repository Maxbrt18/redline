"""Notes d'analyse des six capacités course.

Leur frontmatter ne porte plus aucun état opérationnel : scores, statuts, dates et
prochains tests vivent exclusivement dans PostgreSQL. Les notes gardent seulement
leur identité Obsidian et le raisonnement humain de leur corps.
"""

from __future__ import annotations

from pathlib import Path

from . import vault_layout
from .notes import read_all
from .schema import Schema, field

CAPABILITY_SCHEMA = Schema("note de capacité", [
    field("type", str, choices=("capacite",)),
    field("capacite_num", int, minimum=1, maximum=9),
    field("aliases", list, required=False, item=field("alias", str)),
    field("tags", list, required=False, item=field("tag", str)),
])


def load(vault: Path) -> dict:
    """Charge les notes d'analyse courantes, sans état opérationnel."""
    return read_all(vault / vault_layout.CAPABILITIES, CAPABILITY_SCHEMA)
