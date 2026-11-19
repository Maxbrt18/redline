"""Renders the README images from the engine's own cards.

Everything shown is the fictional athlete of the test suite; the conversation is
illustrative. The banner and the social preview are drawn by hand: their sources are
`banner.svg` and `social-preview.svg`. Needs the `cards` extra and Pillow:

    python docs/assets/build.py
"""

import base64
import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from redline.cards import render

HERE = Path(__file__).resolve().parent
SAMPLES = render.CARDS_DIR / "samples"
PHONE = {"width": 393, "height": 852}
# The logo's bars run past its left edge, so that the mark can bleed off any canvas.
MARK = ("M-2000 173H320A267 267 0 0 1 480 652L606 849H401L309 706H-2000V545H317"
        "A106 106 0 0 0 317 333H-2000Z")
BASE_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; }
html, body { background: #110e0d; color: #f5f0ea; font-family: var(--sans); }
.glow { background: radial-gradient(120% 130% at 100% 0, #700000, #3f0000 62%); }
.display { font-family: var(--display); font-weight: 700; }
.phone { border-radius: 46px; border: 10px solid #2b2523; overflow: hidden; display: block; }
"""


CHAT = [
    ("sam", "6 x 400 faits. Dur à la fin."),
    ("coach", "Lu sur Strava : 3:48 en R1, 3:30 en R6, 186 bpm. La dernière part 18 s sous "
              "la première, c'est elle qui a coûté."),
    ("card", ""),
    ("sam", "RPE 8. Genou à 2/10 sur la fin."),
    ("coach", "Noté, et 2, c'est ton plafond. Demain, 30 min faciles sur le plat. Si le genou "
              "reste marqué au réveil, la qualité de samedi saute."),
]
TURN = [
    ("Received", "A message or a Strava webhook is stored and acknowledged at once."),
    ("Facts first", "The code computes the week, the load and the pain trend for the turn."),
    ("The model reads", "It reads more as the question gets more consequential."),
    ("One transaction", "Facts and plan changes are validated, then applied together or not at all."),
    ("Checked reply", "The reply, the canonical plan and the cards are delivered, once."),
]


def page_html(body: str, css: str = "") -> str:
    fonts = render._font_css()
    tokens = render.TOKENS.read_text(encoding="utf-8")
    return (f'<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><style>'
            f"{fonts}{tokens}{BASE_CSS}{css}</style></head><body>{body}</body></html>")


def shot(browser, html: str, width: int, height: int) -> bytes:
    page = browser.new_page(viewport={"width": width, "height": height},
                            device_scale_factor=2)
    page.set_content(html, wait_until="load")
    page.evaluate("() => document.fonts.ready")
    image = page.screenshot()
    page.close()
    return image


def card(browser, name: str) -> tuple[bytes, int]:
    """A sample card in the dark theme, and its height."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{name}.html"
        with (SAMPLES / f"{name}.json").open(encoding="utf-8") as sample:
            subprocess.run(["redline", f"chart-{name}", "--html", "-o", str(path)], check=True,
                           stdin=sample)
        html = path.read_text(encoding="utf-8").replace('data-theme="light"', 'data-theme="dark"')
    page = browser.new_page(viewport={"width": render.WIDTH, "height": 900},
                            device_scale_factor=2)
    page.set_content(html, wait_until="load")
    page.evaluate("() => document.fonts.ready")
    height = page.evaluate("() => document.getElementById('card').offsetHeight")
    image = page.locator("#card").screenshot()
    page.close()
    return image, height


def data_uri(image: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image).decode()


def chat(browser, workout: bytes) -> bytes:
    bubbles = "".join(
        f'<img class="card" src="{data_uri(workout)}" alt="">' if who == "card"
        else f'<p class="{who}">{text}</p>' for who, text in CHAT)
    return shot(browser, page_html(f"""
      <header><svg viewBox="0 0 1024 1024"><path fill="#d40000" d="{MARK}"/></svg>
        <b>Redline</b></header><main>{bubbles}</main>""", """
      body { width: 393px; height: 852px; overflow: hidden; }
      header { display: flex; align-items: center; gap: 12px; padding: 58px 20px 16px;
               font-size: 19px; border-bottom: 1px solid #2e2826; }
      header svg { width: 36px; height: 36px; border-radius: 50%; background: #3f0000; }
      main { display: flex; flex-direction: column; gap: 10px; padding: 18px 16px; }
      p { max-width: 82%; padding: 11px 15px; border-radius: 20px; font-size: 16px;
          line-height: 1.38; }
      .sam { align-self: flex-end; background: #d40000; color: #fff; }
      .coach { align-self: flex-start; background: #221d1b; }
      .card { width: 82%; border-radius: 16px; }"""), PHONE["width"], PHONE["height"])


def phones(browser, screens: list[bytes]) -> bytes:
    frames = "".join(f'<img class="phone" src="{data_uri(screen)}" alt="">' for screen in screens)
    return shot(browser, page_html(f"<main class=glow>{frames}</main>", """
      main { display: flex; justify-content: center; gap: 44px; padding: 64px 0 0;
             height: 720px; overflow: hidden; }
      .phone { width: 393px; height: 852px; flex: none; }"""), 1400, 720)


def cards(browser, images: list[tuple[bytes, int]]) -> bytes:
    """Equal frames on the brand background, whatever the height of each card."""
    frames = "".join(f'<div><img src="{data_uri(image)}" alt=""></div>' for image, _ in images)
    tallest = max(height for _, height in images)
    height = round(tallest * 640 / render.WIDTH)
    return shot(browser, page_html(f"<main class=glow>{frames}</main>", f"""
      main {{ display: flex; justify-content: center; gap: 40px; padding: 40px; }}
      div {{ width: 640px; height: {height}px; background: #110e0d; border-radius: 20px;
            overflow: hidden; }}
      img {{ width: 640px; display: block; }}"""), 1400, height + 80)


def turn(browser) -> bytes:
    steps = "".join(
        f"<section><b class=display>{number}</b><h3>{title}</h3><p>{text}</p></section>"
        for number, (title, text) in enumerate(TURN, 1))
    return shot(browser, page_html(f"<main class=glow>{steps}</main>", """
      main { display: grid; grid-template-columns: repeat(5, 1fr); gap: 36px;
             padding: 44px 48px; height: 280px; }
      b { display: block; font-size: 58px; line-height: 1; }
      h3 { font-size: 20px; margin: 16px 0 10px; }
      p { font-size: 16px; line-height: 1.4; color: #e3cfc7; }"""), 1400, 280)


def artwork(browser, name: str, width: int, height: int) -> bytes:
    """A hand-drawn SVG at its exact size."""
    page = browser.new_page(viewport={"width": width, "height": height})
    svg = (HERE / name).read_text(encoding="utf-8")
    page.set_content(f"<!doctype html><style>*{{margin:0}}svg{{display:block}}</style>{svg}")
    image = page.screenshot()
    page.close()
    return image


def save(image: bytes, name: str, **options) -> None:
    Image.open(io.BytesIO(image)).convert("RGB").save(HERE / name, **options)


def main() -> None:
    with sync_playwright() as engine:
        browser = engine.chromium.launch(**render._launch_kwargs())
        week, workout = card(browser, "week"), card(browser, "workout")
        conversation = chat(browser, workout[0])
        screens = [conversation]
        save(artwork(browser, "banner.svg", 2560, 720), "banner.webp", quality=96)
        save(artwork(browser, "social-preview.svg", 1280, 640), "social-preview.png", optimize=True)
        save(phones(browser, screens), "phones.webp", quality=86)
        save(cards(browser, [week, workout]), "cards.webp", quality=86)
        save(turn(browser), "turn.webp", quality=88)
        browser.close()


if __name__ == "__main__":
    main()
