"""How the coach names each session kind, keyed by slug.

`short` fits a one-letter-wide calendar cell; `name` titles a card or a screen.
`server/tests/test_session_kinds.py` checks that every kind has both.
"""

from __future__ import annotations

from scripts.model import session_kinds

LABELS = {
    "easy": ("EF", "EF"),
    "long": ("SL", "Sortie longue"),
    "threshold": ("seuil", "Seuil"),
    "hills": ("côtes", "Côtes"),
    "strides": ("lignes", "Lignes"),
    "cadence": ("cad.", "Cadence"),
    "race": ("course", "Course"),
    "hike": ("rando", "Rando"),
    "bike": ("vélo", "Vélo"),
    "strength": ("R", "Renfo"),
    "test": ("test", "Test"),
    "rest": ("repos", "Repos"),
    "wake-trace": ("réveil", "Trace au réveil"),
    "rest-reading": ("repos", "Douleur au repos"),
    "instruments": ("instr.", "Instruments"),
    "palpation": ("palp.", "Palpation"),
}


def short(slug: str) -> str:
    return LABELS[slug][0]


def name(slug: str) -> str:
    return LABELS[slug][1]


def for_page() -> dict[str, dict[str, str]]:
    """What the notebook page needs to name and draw every kind."""
    return {slug: {"short": label, "name": title, "family": session_kinds.family(slug)}
            for slug, (label, title) in LABELS.items()}
