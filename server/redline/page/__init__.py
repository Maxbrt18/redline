"""The notebook, assembled into ONE self-contained document; the guided strength
session is one of its screens, and /s serves the same document (2026-10-09).

Same doctrine as the cards (scripts/cards/render.py): stylesheet, script and
fonts are inlined, so there is no asset path to get wrong between the Mac and the
VPS, no second route to serve, and no half-styled page if one request fails. The
whole thing weighs ~200 kB, hashed into an ETag: after the first open, it is a
304. The notebook's offline shell is that one document, too.

The code is WRITTEN as ES modules -- one file per role, `shared/` for what both
pages use -- and assembled here at startup (`bundle`): imports are checked, then
dropped, and the modules are concatenated in a declared order into one script.
No build step, no bundler to install on the VPS; the source reads like modules
and the page ships like before (2026-10-09). The rules a module follows are
enforced by server/tests/test_page_modules.py.

The design tokens and the fonts are READ FROM THE VAULT (scripts/cards/), which
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

log = logging.getLogger("redline")

PAGE_DIR = Path(__file__).resolve().parent

# (CSS alias, file name, weight or weight range) -- mirrors FONT_FACES in
# scripts/cards/render.py. Duplicated rather than imported: the cards live in the
# vault (a configurable path), the server package must not reach into it to boot.
FONT_FACES = (
    ("Redline Sans", "Inter-latin.woff2", "100 900"),
    ("Redline Display", "BarlowCondensed-SemiBold.woff2", "600"),
    ("Redline Display", "BarlowCondensed-Bold.woff2", "700"),
)


# The page's modules, in evaluation order. A module only declares at its top level
# (functions, constants, listeners): it never calls an import there, so the order
# matters for listener registration only. `carnet/main.js` comes last and starts the
# page. The guided strength session is a screen of the notebook (2026-10-09): its
# modules ship in the same script, and /s serves the same document.
CARNET_SCRIPT = (
    "shared/dom.js", "shared/storage.js", "shared/theme.js",
    "strength/session.js", "strength/link.js", "strength/timers.js", "strength/sound.js",
    "strength/actions.js", "strength/control.js", "strength/review.js", "strength/render.js",
    "strength/send.js", "strength/main.js",
    "carnet/state.js", "carnet/ui.js", "carnet/session.js", "carnet/api.js", "carnet/format.js",
    "carnet/screens/weather.js", "carnet/screens/today.js", "carnet/screens/week.js",
    "carnet/screens/course.js", "carnet/screens/injury.js", "carnet/screens/archives.js",
    "carnet/screens/detail.js",
    "carnet/viewer.js", "carnet/nav.js", "carnet/data.js", "carnet/sheet.js", "carnet/seance.js",
    "carnet/gestures.js", "carnet/main.js",
)
# Stylesheets after tokens.css, in cascade order: a later file may override an earlier one.
CARNET_STYLE = (
    "shared/base.css", "carnet/shell.css", "carnet/screens/today.css", "carnet/screens/week.css",
    "carnet/screens/course.css", "carnet/screens/injury.css", "carnet/screens/archives.css",
    "carnet/screens/detail.css", "carnet/sheet.css", "carnet/viewer.css", "strength/strength.css", "carnet/seance.css",
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


# Le thème avant le premier affichage : épinglé dans le carnet (data-theme) ou celui
# du téléphone, et la bande d'état d'iOS (`theme-color`) qui le suit. Les modules le
# tiennent ensuite à jour (shared/theme.js).
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
    """`display="block"` pour le carnet : les polices sont dans la page, elles arrivent
    avec elle ; `swap` y dessinait une frame en police système, puis tout se décalait."""
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
    """`window.REDLINE_HOW` -- les consignes d'exécution, par slug d'exercice.

    ELLES VOYAGENT DANS LA PAGE, PAS DANS LE SNAPSHOT (2026-07-31). Elles coûtent
    2,5 kB sur un document servi une fois puis en 304 ; le snapshot ne porte que le
    slug, que la prescription possède déjà.

    Ce que ça change, dit franchement : `/s` ne portait AUCUNE donnée, c'était sa
    propriété de sécurité. Elle porte maintenant des consignes de mouvement. Pas
    de donnée de santé, pas de nom, pas de dose, rien de personnel — « Genou
    fléchi à ~30°, amplitude maximale » est dans n'importe quel manuel. Mais la
    page n'est plus une coquille vide, et le dire vaut mieux que le découvrir.
    (Arbitrage de l'athlète, 2026-07-31.)

    Corollaire : le document est assemblé au démarrage. Les consignes font donc
    partie du runtime et leur mise à jour passe par un déploiement du service.
    """
    if not cues:
        return ""
    # `</` échappé : sans ça, une consigne contenant « </script> » fermerait la
    # balise. Aucune ne le fait aujourd'hui ; c'est l'entrée qui décide, pas nous.
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
    guided session of the link (2026-10-09). `cues` maps an exercise slug to its
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


# L'écran de lancement de la PWA iOS, par taille d'iPhone en portrait : (largeur et
# hauteur CSS, densité). iOS n'affiche une image que si elle a exactement la taille de
# l'écran ; sans elle, il ouvre l'app sur un écran blanc, même en thème sombre. Le fond
# uni est la règle d'Apple (le premier écran sans son contenu) et suit tokens.css.
LAUNCH_SCREENS = (
    (440, 956, 3), (430, 932, 3), (428, 926, 3), (420, 912, 3), (414, 896, 3), (414, 896, 2),
    (402, 874, 3), (393, 852, 3), (390, 844, 3), (375, 812, 3), (375, 667, 2),
)
LAUNCH_COLORS = {"light": (0xF7, 0xF5, 0xF2), "dark": (0x11, 0x0E, 0x0D)}
_LAUNCH = re.compile(r"(\d{3,4})x(\d{3,4})-(light|dark)")


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Un PNG uni, bibliothèque standard seulement (module feuille)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height, 9)) + chunk(b"IEND", b""))


@functools.lru_cache(maxsize=32)
def launch_image(name: str) -> bytes | None:
    """`1179x2556-dark` → le PNG de cet écran, ou None si la taille n'est pas un iPhone connu."""
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
    """Coquille du carnet, sans aucune donnée personnelle ni secret ; les consignes
    d'exécution des exercices (`cues`) y voyagent pour la séance guidée."""
    css = _read(cards_dir / "tokens.css", "tokens.css") + _styles(CARNET_STYLE)
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="robots" content="noindex,nofollow">'
        # La bande d'état iOS suit `theme-color` : une seule balise, alimentée par le thème
        # résolu (Auto, Clair ou Sombre) et non par `media`, qui ne connaît que celui du
        # téléphone. Posée avant le premier affichage, puis tenue à jour par la page.
        f"{_THEME_HEAD}"
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<link rel="manifest" href="/carnet-manifest.json">'
        f"{_launch_links()}"
        '<link rel="apple-touch-icon" href="/carnet-icon.png">'
        '<link rel="icon" href="/carnet-icon.png">'
        '<title>Mon carnet — Redline</title>'
        f"<style>{_font_css(cards_dir / 'fonts', 'block')}{css}</style></head><body>"
        f"{_read(PAGE_DIR / 'carnet' / 'carnet.html', 'carnet.html')}"
        f"{_cue_script(cues)}"
        f"{_kinds_script(kinds)}"
        f"<script>{bundle(CARNET_SCRIPT)}</script>"
        '</body></html>'
    )
