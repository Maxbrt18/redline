"""Local triage of phrasings whose class needs no interpretation."""

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
    """Return a decision only when a high-precision rule suffices."""
    value = _normalise(text)
    if not value:
        return Triage("N0")
    if SOCIAL.fullmatch(value.strip(" .!?,;:")):
        return Triage("N0")
    # Any open phrase needs its meaning in context. Words like "finally", "pain" or
    # "I doubt" are neither a decision to change the program nor a write
    # authorization.
    return None
