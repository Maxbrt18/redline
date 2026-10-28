"""Triage local des formulations dont la classe ne demande aucune interprétation."""

from __future__ import annotations

import re
import unicodedata

from .agent import Triage


def _normalise(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    return re.sub(r"\s+", " ", value).strip()


SOCIAL = re.compile(
    r"(?:salut|hello|coucou|merci(?: beaucoup)?|bonne nuit)", re.IGNORECASE)
def deterministic_triage(text: str) -> Triage | None:
    """Retourne une décision seulement quand une règle haute précision suffit."""
    value = _normalise(text)
    if not value:
        return Triage("N0")
    if SOCIAL.fullmatch(value.strip(" .!?,;:")):
        return Triage("N0")
    # Toute phrase ouverte demande son sens en contexte. Des mots comme
    # « finalement », « douleur » ou « je doute » ne constituent ni une décision
    # de modifier le programme ni une autorisation d'écriture.
    return None
