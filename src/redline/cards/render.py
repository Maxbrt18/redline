"""HTML -> PNG for the Redline cards, via headless Chromium (Playwright).

The browser measures the text and flexbox distributes what is left, so an overlap is
not a bug to fix but a state the layout cannot reach. The document is fully
self-contained (stylesheet and fonts inlined), which is also what makes `--html` a
file you can open in devtools -- that IS the design loop.

Cards use the browser renderer shared by the whole visual layer.
"""

import base64
import math
import os
import sys
from pathlib import Path

from redline.model.errors import InputError

CARDS_DIR = Path(__file__).resolve().parent
FONTS_DIR = CARDS_DIR / "fonts"
TOKENS = CARDS_DIR / "tokens.css"   # palette and faces, shared with the session page
THEME = CARDS_DIR / "theme.css"     # card geometry, not shared

WIDTH = 1080          # card width in CSS px (phone-first)
SCALE = 2             # device pixel ratio -> 2160 px PNG
MAX_HEIGHT = 2400     # hard ceiling: past this Messenger shrinks the card into mush
# The cards are images: they cannot follow the phone's theme, so they pin one.
# "light" or "dark" -- both palettes live in tokens.css under the same names.
CARD_THEME = "light"

# (alias exposed to the CSS, file, weight or weight range). Inter is a variable
# font, so one file covers 100-900. Missing files degrade to the system stack --
# never a hard failure, but see fonts/NOTICE.md for why that matters.
FONT_FACES = (
    ("Redline Sans", "Inter-latin.woff2", "100 900"),
    ("Redline Display", "BarlowCondensed-SemiBold.woff2", "600"),
    ("Redline Display", "BarlowCondensed-Bold.woff2", "700"),
)


class CardOverflow(InputError):
    """The layout could not hold its content. Raised BEFORE writing any PNG."""


# --------------------------------------------------------------------- document

def _font_css() -> str:
    rules = []
    missing = []
    for family, filename, weight in FONT_FACES:
        path = FONTS_DIR / filename
        if not path.exists():
            missing.append(filename)
            continue
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            f"@font-face{{font-family:'{family}';font-weight:{weight};"
            f"font-style:normal;font-display:block;"
            f"src:url(data:font/woff2;base64,{payload}) format('woff2');}}"
        )
    if missing:
        print(f"[cards] polices absentes, repli sur la pile système : "
              f"{', '.join(missing)}", file=sys.stderr)
    return "".join(rules)


def document(body: str, *, width: int = WIDTH) -> str:
    css = TOKENS.read_text(encoding="utf-8") + THEME.read_text(encoding="utf-8")
    return (
        f'<!doctype html><html lang=fr data-theme="{CARD_THEME}"><head><meta charset=utf-8>'
        f"<style>{_font_css()}</style>"
        f"<style>:root{{--card-width:{width}px}}{css}</style>"
        f"</head><body>{body}</body></html>"
    )


# ------------------------------------------------------------------ the guard

# Runs in the page once layout has settled. Returns a list of human-readable
# problems; an empty list is the only acceptable result.
_GUARD_JS = r"""
({maxHeight}) => {
  const card = document.getElementById('card');
  if (!card) return ['aucun élément #card dans le document'];
  const box = card.getBoundingClientRect();
  const problems = [];

  const name = (el) => {
    const cls = (el.getAttribute && el.getAttribute('class')) || '';
    const txt = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 46);
    return `<${el.tagName.toLowerCase()}${cls ? ' class="' + cls + '"' : ''}>` +
           (txt ? ` « ${txt} »` : '');
  };

  if (card.offsetHeight > maxHeight)
    problems.push(`card trop haute : ${Math.round(card.offsetHeight)} px (plafond ${maxHeight})`);
  if (document.documentElement.scrollWidth > card.offsetWidth + 1)
    problems.push('la page déborde horizontalement de la card');

  for (const el of card.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;

    // 1. nothing may stick out of the card itself
    if (r.right > box.right + 0.5 || r.left < box.left - 0.5)
      problems.push('sort de la card : ' + name(el));
    else if (r.bottom > box.bottom + 0.5)
      problems.push('dépasse en bas de la card : ' + name(el));

    // 2. nothing may stick out of its own <svg> viewport (silently clipped)
    const svg = el.ownerSVGElement;
    if (svg) {
      const sb = svg.getBoundingClientRect();
      if (r.right > sb.right + 0.5 || r.left < sb.left - 0.5)
        problems.push('sort du graphe : ' + name(el));
      continue;                       // scrollWidth is meaningless inside SVG
    }

    // 3. no text may be clipped inside its own box
    if (el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1)
      problems.push('texte tronqué en largeur : ' + name(el));
    if (el.clientHeight > 0 && el.scrollHeight > el.clientHeight + 1 &&
        getComputedStyle(el).overflow !== 'visible')
      problems.push('texte tronqué en hauteur : ' + name(el));
  }
  return [...new Set(problems)].slice(0, 8);
}
"""


def _launch_kwargs() -> dict:
    """CHROMIUM_PATH lets the VPS use its apt chromium if the Playwright download
    is unavailable for the platform (Oracle/Ampere is arm64)."""
    path = os.environ.get("CHROMIUM_PATH")
    return {"executable_path": path} if path else {}


# ---------------------------------------------------------------------- render

def render(body: str, out_path: str, *, width: int = WIDTH, scale: int = SCALE,
           max_height: int = MAX_HEIGHT) -> None:
    """Render a card body to a PNG. Raises CardOverflow instead of writing a
    broken card -- the bot then sends its text without an attachment, which beats
    an unreadable image."""
    from playwright.sync_api import sync_playwright

    doc = document(body, width=width)
    with sync_playwright() as engine:
        browser = engine.chromium.launch(**_launch_kwargs())
        try:
            context = browser.new_context(
                viewport={"width": width, "height": 900},
                device_scale_factor=scale,
                # Fonts are embedded; this only pins the fallback stack's metrics.
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.set_content(doc, wait_until="load")
            page.evaluate("() => document.fonts.ready")

            problems = page.evaluate(_GUARD_JS, {"maxHeight": max_height})
            if problems:
                raise CardOverflow("\n  - ".join(["mise en page invalide :", *problems]))

            height = page.evaluate("() => document.getElementById('card').offsetHeight")
            page.set_viewport_size({"width": width, "height": math.ceil(height)})
            page.locator("#card").screenshot(path=out_path)
        finally:
            browser.close()


def emit(body: str, out_path: str, *, html_only: bool = False, **kwargs) -> None:
    """CLI tail shared by the four card scripts.

    Prints the produced path on stdout (the outbox contract) and exits non-zero
    with a readable message if the layout could not hold.
    """
    target = Path(out_path)
    if html_only:
        target = target.with_suffix(".html")
        target.write_text(document(body, width=kwargs.get("width", WIDTH)),
                          encoding="utf-8")
        print(target)
        return
    try:
        render(body, str(target), **kwargs)
    except CardOverflow as exc:
        raise InputError(f"Card non générée — {exc}") from exc
    print(target)
