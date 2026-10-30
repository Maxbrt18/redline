"""The burst protocol, shared between generation (agent) and transport (messenger).

Both split on the same boundaries and apply the same cap, so the rule MUST live in
one place. `strip_links` is the ONLY mechanical rewrite allowed on the coach's
output: it is pure syntax, impossible in normal prose. Everything else -- tone,
dashes, Markdown -- comes from the STYLE_PROMPT. If a tic reappears, tighten the
prompt; never add a regex here.

A paragraph is a bubble. Cutting on an explicit `---` alone would deliver a
three-paragraph answer as one wall of text with blank lines inside it -- which nobody
types on Messenger. A blank line therefore ends a bubble on its own, and `---`
remains the way to force a cut where no blank line exists.

Two exceptions, both about lists, because a list is not a new thought:
  - a paragraph ending in a colon announces what follows, so it stays glued to it;
  - a paragraph that *is* a list item stays with what introduced it -- otherwise a
    five-item list would arrive as five texts.
"""

import re

BURST_SEPARATOR = re.compile(r"(?m)^[ \t]*---[ \t]*$")
BURST_MAX = 5

_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*•+]\s|\d+[.)]\s)")

# How much of the next paragraph the streaming path needs before it can tell a list
# item from prose. A lone "- " is not yet a full list item: judging it too early
# would cut a list into bubbles, one token before the evidence arrived.
_LOOKAHEAD = 3

_LINKED = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_MDLINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def strip_links(text: str) -> str:
    text = _LINKED.sub(lambda m: m.group(2) or m.group(1), text)
    return _MDLINK.sub(r"\1", text)


def _glued(before: str, after: str) -> bool:
    """Whether a blank line between these two paragraphs is NOT a bubble boundary."""
    return before.rstrip().endswith(":") or bool(_LIST_ITEM.match(after))


def _boundary(text: str, partial: bool):
    """The next bubble boundary as (start, end), or None if there is none -- or, when
    `partial`, none that can be judged yet on the text received so far."""
    best = None
    for match in BURST_SEPARATOR.finditer(text):
        best = (match.start(), match.end())
        break
    for match in _BLANK_LINE.finditer(text):
        if best is not None and match.start() > best[0]:
            break
        after = text[match.end():]
        if partial and len(after.strip()) < _LOOKAHEAD:
            # The paragraph after the blank line is still arriving: wait for it rather
            # than guess. In batch mode there is nothing more coming, so a short tail
            # is simply a short bubble.
            return None
        if _glued(text[:match.start()], after):
            continue
        if best is None or match.start() < best[0]:
            best = (match.start(), match.end())
        break
    return best


def split_bubbles(text: str, *, partial: bool = False) -> tuple[list[str], str]:
    """Cut `text` into bubbles, returning (complete bubbles, undecided remainder).

    In batch mode the remainder is simply the last bubble. In `partial` mode -- the
    streaming path, called on a buffer that is still growing -- it is what cannot be
    judged yet and must stay in the buffer.
    """
    bubbles: list[str] = []
    rest = text
    while True:
        found = _boundary(rest, partial)
        if found is None:
            break
        head, rest = rest[:found[0]].strip(), rest[found[1]:]
        if head:
            bubbles.append(head)
    return bubbles, rest


def burst_parts(text: str) -> list[str]:
    """The whole reply as the texts the athlete will receive, capped at BURST_MAX.

    Past the cap the tail is rejoined rather than dropped: a burst that is too long is
    a style problem for the prompt to fix, never a reason to lose a sentence.
    """
    bubbles, rest = split_bubbles(text)
    if rest.strip():
        bubbles.append(rest.strip())
    if not bubbles:
        return [text]
    if len(bubbles) > BURST_MAX:
        bubbles = [*bubbles[:BURST_MAX - 1], "\n\n".join(bubbles[BURST_MAX - 1:])]
    return bubbles
