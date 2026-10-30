"""Glyphs that a phone keyboard cannot type, and which must therefore not enter anything
that ends up in front of the coach or the athlete.

The typographic rule has two channels: the prose written in code, guarded by
`BANNED_GLYPHS` plus a test that scans injected texts, and the data channel. The largest
block injected on each prescriptive turn is the program itself, rendered by
`weekly_plan.as_json()`: texts the coach wrote earlier and the service puts back in front
of it later (for example a gate `question` interpolated verbatim into the hidden
instruction, or a `note` sent to the athlete's phone). Without a check on that channel,
the instruction is contradicted by its own context, and the loop closes on itself since
the coach writes the next revision after reading the previous one.

Why a REFUSAL and not a rewrite: `redline/textutils.py` holds that `strip_links` is the
only mechanical rewrite allowed on coach output, and that the rest is fixed in the
prompt. A silent normalization here would clean the trace without ever teaching the rule.

Why at WRITE time and not at read time: the rule concerns the producer's keyboard, not
the persisted structure. Only the incoming text is judged; the plan schema stays single.
"""

from __future__ import annotations

from redline.coach.fr import typography as wording

#: What a phone keyboard cannot type. `src/redline/service/prompts.py` re-exports this
#: list: the prompt that forbids them and the check that enforces it cannot diverge if
#: they read the same constant.
BANNED_GLYPHS = "—–·…«»“”’→•−≥≤⚠⭐"

#: What to type instead, named so that the error message opens the correct path instead
#: of merely closing the wrong one (a guard that opens no correct path breeds its own
#: workaround).


def banned_in(text: str) -> list[str]:
    """The banned glyphs present, in order of first appearance."""
    seen: list[str] = []
    for char in text or "":
        if char in BANNED_GLYPHS and char not in seen:
            seen.append(char)
    return seen


def errors(text: str, where: str, label: str) -> list[str]:
    """The refusal, with the offending glyph, its context and what to type instead."""
    problems = []
    for glyph in banned_in(text):
        index = text.find(glyph)
        around = " ".join(text[max(0, index - 40):index + 40].split())
        problems.append(
            f"{where}: {label} contains {glyph!r}, which a phone keyboard does not have "
            f"(around {around!r}). This text is fed back to the coach every turn, and "
            f"title/note go to the athlete's phone. Instead: "
            f"{wording.REPLACEMENTS.get(glyph, wording.ANY_KEY)}.")
    return problems
