"""What the coach rereads of its own messages: the substance, without its tics.

The service re-injects the coach's latest messages on each turn (relational memory,
summaries of proactive messages, conversation search). It rereads them as examples and
copies their markers: once one message carries an emoji, later ones tend to as well, even
a reprimand or a stop instruction, and the tic survives a fresh session and a changed
style prompt.

The reread therefore keeps what was said and strips the emoji and the opening greeting,
which carry no information. The stored text stays intact: only the copy the coach rereads
is cleaned. The athlete's messages never pass through here.
"""

from __future__ import annotations

import re

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
# The greeting, and the name after it when punctuation closes it ("Salut Alex, ...").
OPENING = re.compile(
    r"^(?i:salut|hello|hey|coucou|bonjour|bonsoir|yo)"
    r"(?:\s+[A-ZÀ-Þ][\w'’-]*(?=\s*[,!.]))?\s*[,!.]?\s+")
_SPACES = re.compile(r"[ \t]{2,}")


def muted(text: str) -> str:
    """The coach message as it is reread: without emoji or opening greeting."""
    plain = OPENING.sub("", EMOJI.sub("", text).strip(), count=1)
    plain = _SPACES.sub(" ", plain).replace(" .", ".").replace(" ,", ",").strip()
    return plain[:1].upper() + plain[1:] if plain else "(réaction sans texte)"
