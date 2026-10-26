"""HTML building blocks shared by the four Redline cards.

Every user-supplied string goes through esc(): the JSON is compiled by Claude from
the vault, so it is trusted-ish, but an unescaped "&" or "<" would silently break
the layout -- and a card that renders wrong is worse than no card.

The components here own the MARKUP; theme.css owns the LOOK. Nothing in this file
computes a position or a size: that is the whole point of the move away from
matplotlib. If you find yourself adding a pixel here, put it in the stylesheet.
"""

from html import escape


def esc(value) -> str:
    """Any value -> HTML-safe text."""
    return escape(str(value), quote=True)


def classes(*names) -> str:
    """Drop the falsy ones and join: classes("row", is_key and "is-key")."""
    return " ".join(str(n) for n in names if n)


def header(title: str, subtitle: str = "") -> str:
    """Card banner: wordmark, title, meta line. Height follows the content."""
    meta = f'<p class="head__meta">{esc(subtitle)}</p>' if subtitle else ""
    return (
        '<header class="head">'
        '<p class="head__wordmark">Redline</p>'
        f'<h1 class="head__title">{esc(title)}</h1>'
        f"{meta}"
        "</header>"
    )


def section(label: str) -> str:
    return f'<p class="section">{esc(label)}</p>'


def tiles(items: list) -> str:
    """A strip of figures: {label, value, sub?, status?, kind?}.

    kind="text" switches the value to the body face: the condensed display font is
    made for figures, and a sentence set in it reads like a headline.

    status="bad" -- the instrument crossed its limit -- turns the VALUE red. "ok"
    and "warn" draw nothing: there is no status dot any more (2026-10-01, a dot
    beside a label reads as a badge). A milder state belongs in the words of the
    value or of its sub line.
    """
    if not items:
        return ""
    cells = []
    for item in items:
        sub = item.get("sub") or ""
        sub_html = f'<p class="tile__sub">{esc(sub)}</p>' if sub else ""
        value_cls = classes("tile__value",
                            item.get("kind") == "text" and "tile__value--text",
                            item.get("status") == "bad" and "tile__value--over")
        cells.append(
            '<div class="tile">'
            f'<p class="tile__label">{esc(item["label"])}</p>'
            f'<p class="{value_cls}">{esc(item["value"])}</p>'
            f"{sub_html}"
            "</div>"
        )
    return f'<div class="tiles">{"".join(cells)}</div>'


def verdict(message: str, status: str) -> str:
    """The closing judgement, a line of prose. Set in red when it reports a limit
    crossed ("bad"), in ink otherwise -- red means exactly that on every card."""
    cls = classes("verdict", status == "bad" and "verdict--over")
    return f'<p class="{cls}">{esc(message)}</p>'


def footer(note: str = "", rule: str = "") -> str:
    if not (note or rule):
        return ""
    parts = ['<footer class="foot">']
    if note:
        parts.append(f'<p class="foot__note">{esc(note)}</p>')
    if rule:
        parts.append(f'<p class="foot__rule">{esc(rule)}</p>')
    parts.append("</footer>")
    return "".join(parts)


def legend(items: list) -> str:
    """Legend entries: {kind, label}, kind a swatch in theme.css: "data" (volume
    done), "ghost" (the plan, the recoveries), "effort" and "over" (a rep under
    and past its limit). A legend explains marks only:
    a fact with no mark belongs in the subtitle."""
    entries = "".join(
        f'<span class="legend__item">'
        f'<span class="legend__swatch legend__swatch--{esc(item["kind"])}"></span>'
        f'{esc(item["label"])}</span>'
        for item in items
    )
    return f'<div class="legend">{entries}</div>'
