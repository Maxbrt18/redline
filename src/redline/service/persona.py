"""The athlete's first name is written nowhere in the engine: it is rendered on output.

Service texts (prompts, turn notes, instructions) carry the token `⟨athlète⟩` in place
of the name. It is replaced by `[athlete] name` from `redline.toml` when the text
leaves the process: towards the model (the Claude and Codex adapters) and towards
Messenger. A text that left without passing here would show the token as is, which is
visible; a hard-coded name would silently leak into another athlete's instance.

French elides before a vowel: "de ⟨athlète⟩" becomes "d'Alex" but stays "de Marie".
Rendering handles this so templates are written once.
"""

from __future__ import annotations

import re

TOKEN = "⟨athlète⟩"

# "de", "que", "jusque" before a name starting with a vowel or a silent h.
_ELISION = re.compile(r"\b([Dd]e|[Qq]ue|[Jj]usque) " + re.escape(TOKEN))
_VOWEL = re.compile(r"^[AEIOUYÂÊÎÔÛÉÈÀHaeiouyâêîôûéèàh]")


def render(text: str, name: str) -> str:
    """The text with the name in place of the token, elision included."""
    if TOKEN not in text:
        return text
    if _VOWEL.match(name):
        text = _ELISION.sub(lambda match: f"{match.group(1)[:-1]}'{name}", text)
    return text.replace(TOKEN, name)
