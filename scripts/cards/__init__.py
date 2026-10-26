"""Redline cards: HTML/CSS card generation for the Messenger coach.

    from cards import html, render
    render.emit(html.header("Titre") + body, args.out, html_only=args.html)

HTML/CSS is rendered through a browser so the same layout primitives serve every card:
matplotlib has no text layout, so every card height was a hand-tuned constant and
a long label drew itself on top of its neighbour. The browser measures text; the
stylesheet distributes what is left.
"""

from . import charts, format, html, render

__all__ = ["charts", "format", "html", "render"]
