"""The keyboard the coach writes with, in the language it writes."""

from __future__ import annotations

#: What to type instead of each glyph a phone keyboard does not have.
REPLACEMENTS = {
    "—": "une virgule, un point, ou deux phrases",
    "–": "un trait d'union simple (-)",
    "·": "une virgule ou un point",
    "…": "trois points (...)",
    "«": 'un guillemet droit (")',
    "»": 'un guillemet droit (")',
    "“": 'un guillemet droit (")',
    "”": 'un guillemet droit (")',
    "’": "l'apostrophe droite (')",
    "→": "« donne », « devient », ou deux phrases",
    "•": "un tiret simple (-)",
    "−": "le signe moins du clavier (-)",
    "≥": "« au moins »",
    "≤": "« au plus »",
    "⚠": "rien, ou le mot qui porte l'alerte",
    "⭐": "rien",
}

ANY_KEY = "un signe du clavier"
