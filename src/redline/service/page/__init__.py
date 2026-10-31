"""The notebook, assembled into ONE self-contained document; the guided strength
session is one of its screens, and /s serves the same document.

Same doctrine as the cards (src/redline/cards/render.py): stylesheet, script and
fonts are inlined, so there is no asset path to get wrong between the Mac and the
VPS, no second route to serve, and no half-styled page if one request fails. The
whole thing weighs ~200 kB, hashed into an ETag: after the first open, it is a
304. The notebook's offline shell is that one document, too.

The code is WRITTEN as ES modules -- one file per role, `shared/` for what both
pages use -- and assembled here at startup (`bundle`): imports are checked, then
dropped, and the modules are concatenated in a declared order into one script.
No build step, no bundler to install on the VPS; the source reads like modules
and the page ships as one script. The rules a module follows are
enforced by tests/test_page_modules.py.

The design tokens and the fonts are READ FROM THE VAULT (src/redline/cards/), which
is where they already live for the cards -- the page borrows the skin, never
copies it. If a file is missing the page still renders, just in the system font
stack: an asset is never a reason to fail.

Leaf module: standard library only.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import logging
import posixpath
import re
import struct
import zlib
from pathlib import Path

from redline.coach.fr import notebook as notebook_text

log = logging.getLogger("redline")

PAGE_DIR = Path(__file__).resolve().parent

# (CSS alias, file name, weight or weight range) -- mirrors FONT_FACES in
# src/redline/cards/render.py. Duplicated rather than imported: the cards live in the
# vault (a configurable path), the server package must not reach into it to boot.
FONT_FACES = (
    ("Redline Sans", "Inter-latin.woff2", "100 900"),
    ("Redline Display", "BarlowCondensed-SemiBold.woff2", "600"),
    ("Redline Display", "BarlowCondensed-Bold.woff2", "700"),
)


# The page's modules, in evaluation order. A module only declares at its top level
# (functions, constants, listeners): it never calls an import there, so the order
# matters for listener registration only. `notebook/main.js` comes last and starts the
# page. The guided strength session is a screen of the notebook: its modules ship in
# the same script, and /s serves the same document.
NOTEBOOK_SCRIPT = (
    "shared/dom.js", "shared/storage.js", "shared/theme.js",
    "strength/session.js", "strength/link.js", "strength/timers.js", "strength/sound.js",
    "strength/actions.js", "strength/control.js", "strength/review.js", "strength/render.js",
    "strength/send.js", "strength/main.js",
    "notebook/state.js", "notebook/ui.js", "notebook/session.js", "notebook/api.js", "notebook/format.js",
    "notebook/screens/weather.js", "notebook/screens/today.js", "notebook/screens/week.js",
    "notebook/screens/raceplan.js", "notebook/screens/course.js", "notebook/screens/injury.js",
    "notebook/screens/archives.js",
    "notebook/screens/detail.js",
    "notebook/viewer.js", "notebook/nav.js", "notebook/data.js", "notebook/sheet.js", "notebook/workout.js",
    "notebook/gestures.js", "notebook/main.js",
)
# Stylesheets after tokens.css, in cascade order: a later file may override an earlier one.
NOTEBOOK_STYLE = (
    "shared/base.css", "notebook/shell.css", "notebook/screens/today.css", "notebook/screens/week.css",
    "notebook/screens/course.css", "notebook/screens/injury.css", "notebook/screens/archives.css",
    "notebook/screens/detail.css", "notebook/sheet.css", "notebook/viewer.css", "strength/strength.css", "notebook/workout.css",
)

_IMPORT = re.compile(r"^import \{([^}]*)\} from '([^']+)';\n", re.MULTILINE)
_EXPORT = re.compile(r"^export (?=(?:async )?function |const |let |class )", re.MULTILINE)
_DECLARED = re.compile(r"^(?:export )?(?:async )?(?:function\*? |const |let |class )([A-Za-z_$][\w$]*)", re.MULTILINE)
_EXPORTED = re.compile(r"^export (?:async )?(?:function\*? |const |let |class )([A-Za-z_$][\w$]*)", re.MULTILINE)


@functools.lru_cache(maxsize=4)
def bundle(modules: tuple[str, ...]) -> str:
    """One classic script from ES modules: imports checked then dropped, `export`
    keywords removed, modules concatenated in the given order inside one function
    scope. A broken import is a ValueError at startup, never a blank page."""
    sources = {name: (PAGE_DIR / name).read_text(encoding="utf-8") for name in modules}
    exported = {name: set(_EXPORTED.findall(src)) for name, src in sources.items()}
    owners: dict[str, str] = {}
    parts = []
    for name, src in sources.items():
        for names, target in _IMPORT.findall(src):
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if resolved not in sources:
                raise ValueError(f"{name} imports {target}, which is not in this page")
            missing = {item.strip() for item in names.split(",")} - exported[resolved]
            if missing:
                raise ValueError(f"{name} imports {sorted(missing)} from {target}, which does not export them")
        code = _EXPORT.sub("", _IMPORT.sub("", src))
        if re.search(r"^(?:import|export)\b", code, re.MULTILINE):
            raise ValueError(f"{name}: only `import {{...}} from '...';` and exported declarations are supported")
        for declared in _DECLARED.findall(src):
            if declared in owners:
                raise ValueError(f"{declared} is declared by both {owners[declared]} and {name}")
            owners[declared] = name
        parts.append(f"// ---- {name}\n{code}")
    script = "(() => {\n'use strict';\n" + "\n".join(parts) + "})();\n"
    if "</script" in script.lower():
        raise ValueError("a module contains </script, which would close the inline script")
    return script


def _styles(files: tuple[str, ...]) -> str:
    return "".join(_read(PAGE_DIR / name, name) for name in files)


# The theme before first paint: the one pinned in the notebook (data-theme) or the
# phone's, and the iOS status bar (`theme-color`) that follows it. The modules keep it
# up to date afterwards (shared/theme.js).
_THEME_HEAD = (
    '<meta name="theme-color" content="#f7f5f2">'
    "<script>(()=>{try{const t=localStorage.getItem('carnet-theme');"
    "if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;"
    "const d=t==='dark'||(t!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches);"
    "document.querySelector('meta[name=theme-color]').content=d?'#110e0d':'#f7f5f2'}catch(e){}})()</script>"
)


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Session page: %s unreadable (%s)", what, exc)
        return ""


def _font_css(fonts_dir: Path, display: str = "swap") -> str:
    """`display="block"` for the notebook: the fonts are inlined in the page and arrive
    with it; `swap` would paint a frame in the system font, then shift everything."""
    rules = []
    for family, filename, weight in FONT_FACES:
        path = fonts_dir / filename
        try:
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            log.warning("Session page: font missing, falling back to the system "
                        "stack (%s)", filename)
            continue
        rules.append(
            f"@font-face{{font-family:'{family}';font-weight:{weight};"
            f"font-style:normal;font-display:{display};"
            f"src:url(data:font/woff2;base64,{payload}) format('woff2');}}"
        )
    return "".join(rules)


def _cue_script(cues) -> str:
    """`window.REDLINE_HOW` -- the execution cues, by exercise slug.

    THEY TRAVEL IN THE PAGE, NOT IN THE SNAPSHOT. They cost about 2.5 kB on a document
    served once then answered with a 304; the snapshot only carries the slug, which
    the prescription already owns.

    Consequence: `/s` no longer carries zero data, it carries movement cues. No health
    data, no name, no dose, nothing personal -- a cue such as "knee bent to ~30
    degrees, full range" is in any manual. The page is no longer an empty shell.

    The document is assembled at startup, so the cues are part of the runtime and
    updating them takes a deployment of the service.
    """
    if not cues:
        return ""
    # `</` is escaped: otherwise a cue containing "</script>" would close the tag.
    # The input decides what a cue contains, not us.
    payload = json.dumps(cues, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"<script>window.REDLINE_HOW={payload}</script>"


def _kinds_script(kinds) -> str:
    """`window.REDLINE_KINDS` -- each session kind's labels and family, by slug.

    The page names and draws a kind from this table only; it keeps no list of its own.
    """
    if not kinds:
        return ""
    payload = json.dumps(kinds, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"<script>window.REDLINE_KINDS={payload}</script>"


def document(cards_dir: Path, cues=None, kinds=None) -> str:
    """The page served at /s: the notebook itself, which opens straight onto the
    guided session of the link. `cues` maps an exercise slug to its
    execution cues.

    The cues are PASSED IN, not read here: this module is a declared leaf (standard
    library only), and reading a note's frontmatter needs the model layer and PyYAML.
    """
    return notebook_document(cards_dir, cues, kinds)


def build(cards_dir: Path, cues=None, kinds=None) -> tuple:
    """(document, etag). Assembled once at startup: the page has no per-request
    content; personal data is resolved from a fragment capability. The tag is the
    bare hash; Flask's set_etag() adds the quotes."""
    html = document(cards_dir, cues, kinds)
    return html, hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]


# The iOS PWA launch screen, per portrait iPhone size: (CSS width, CSS height, density).
# iOS only shows an image of exactly the screen's size; without one it opens the app on
# a white screen, even in dark theme. The plain fill follows Apple's rule (the first
# screen without its content) and tracks tokens.css.
LAUNCH_SCREENS = (
    (440, 956, 3), (430, 932, 3), (428, 926, 3), (420, 912, 3), (414, 896, 3), (414, 896, 2),
    (402, 874, 3), (393, 852, 3), (390, 844, 3), (375, 812, 3), (375, 667, 2),
)
LAUNCH_COLORS = {"light": (0xF7, 0xF5, 0xF2), "dark": (0x11, 0x0E, 0x0D)}
_LAUNCH = re.compile(r"(\d{3,4})x(\d{3,4})-(light|dark)")


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A plain-colour PNG, standard library only (leaf module)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height, 9)) + chunk(b"IEND", b""))


@functools.lru_cache(maxsize=32)
def launch_image(name: str) -> bytes | None:
    """`1179x2556-dark` -> the PNG for that screen, or None if the size is not a known iPhone."""
    match = _LAUNCH.fullmatch(name)
    if not match:
        return None
    width, height, scheme = int(match[1]), int(match[2]), match[3]
    if not any((w * d, h * d) == (width, height) for w, h, d in LAUNCH_SCREENS):
        return None
    return _png(width, height, LAUNCH_COLORS[scheme])


def _launch_links() -> str:
    links = []
    for width, height, density in LAUNCH_SCREENS:
        device = (f"(device-width: {width}px) and (device-height: {height}px) and "
                  f"(-webkit-device-pixel-ratio: {density}) and (orientation: portrait)")
        for scheme in ("dark", "light"):
            links.append(f'<link rel="apple-touch-startup-image" media="{device} and '
                         f'(prefers-color-scheme: {scheme})" '
                         f'href="/carnet-launch/{width * density}x{height * density}-{scheme}.png">')
    return "".join(links)


def notebook_document(cards_dir: Path, cues=None, kinds=None) -> str:
    """The notebook shell, with no personal data or secret; the exercises' execution
    cues (`cues`) travel in it for the guided session."""
    css = _read(cards_dir / "tokens.css", "tokens.css") + _styles(NOTEBOOK_STYLE)
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="robots" content="noindex,nofollow">'
        # The iOS status bar follows `theme-color`: a single tag, fed by the resolved
        # theme (Auto, Light or Dark) and not by `media`, which only knows the phone's.
        # Set before first paint, then kept up to date by the page.
        f"{_THEME_HEAD}"
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<link rel="manifest" href="/carnet-manifest.json">'
        f"{_launch_links()}"
        '<link rel="apple-touch-icon" href="/carnet-icon.png">'
        '<link rel="icon" href="/carnet-icon.png">'
        f'<title>{notebook_text.PAGE_TITLE}</title>'
        f"<style>{_font_css(cards_dir / 'fonts', 'block')}{css}</style></head><body>"
        f"{_read(PAGE_DIR / 'notebook' / 'notebook.html', 'notebook.html')}"
        f"{_cue_script(cues)}"
        f"{_kinds_script(kinds)}"
        f"<script>{bundle(NOTEBOOK_SCRIPT)}</script>"
        '</body></html>'
    )
