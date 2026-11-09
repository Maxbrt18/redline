"""Redline cards: HTML/CSS card generation for the Messenger coach.

    from redline.cards import html, render
    render.emit(html.header("Titre") + body, args.out, html_only=args.html)

HTML/CSS is rendered through a browser so the same layout primitives serve every card:
the browser measures text, so no card height is a hand-tuned constant and a long label
never draws over its neighbour; the stylesheet distributes what is left.
"""

from . import charts, format, html, render

__all__ = ["charts", "format", "html", "render"]
