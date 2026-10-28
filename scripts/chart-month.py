#!/usr/bin/env python3
"""Monthly magazine -- volume, the 6-capability tracks, race countdown.

Form: stat tiles for the month's key figures, thin columns for km per week, and
the race-capability cockpit as horizontal progress tracks (0..scale) -- more
honest and legible than a radar. Countdown to the race as the closing line.

Input: MONTH_SCHEMA in scripts/model/cards.py, from --input (stdin outside a turn).

Usage:  python3 scripts/chart-month.py -o server/outbox/month-2026-07.png < data.json
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cards import archive, charts, cli, html, render  # noqa: E402
from scripts.cards.format import fmt_num  # noqa: E402
from scripts.model import entrypoint  # noqa: E402
from scripts.model.cards import MONTH_SCHEMA  # noqa: E402


def build_tiles(data: dict) -> list[dict]:
    tiles = []
    if data.get("km") is not None:
        tiles.append({"label": "Volume", "value": f"{fmt_num(data['km'])} km"})
    if data.get("dplus") is not None:
        tiles.append({"label": "Dénivelé", "value": f"{fmt_num(data['dplus'])} m D+"})
    if data.get("sessions") is not None:
        tiles.append({"label": "Séances", "value": f"{data['sessions']}"})
    return tiles


def capability_tracks(caps: dict, scale: float) -> str:
    """One rail per capability. The name cell may shrink and wrap, the rail keeps
    a minimum width, the score never moves: no capability name can push the score
    off the card, however long it is."""
    rows = []
    for name, raw in caps.items():
        score = min(max(float(raw or 0), 0), scale)
        filled = score / scale * 100 if scale else 0
        rows.append(
            '<div class="track">'
            f'<span class="track__name">{html.esc(name)}</span>'
            f'<span class="track__rail"><span class="track__fill" '
            f'style="width:{filled:.1f}%"></span></span>'
            f'<span class="track__value">{fmt_num(score)}</span>'
            "</div>"
        )
    return f'<div class="tracks">{"".join(rows)}</div>'


def countdown(data: dict) -> str:
    if not data.get("race_date"):
        return ""
    race_day = date.fromisoformat(data["race_date"])
    days_left = (race_day - date.today()).days
    headline = f"J-{days_left}" if days_left >= 0 else "couru !"
    sub = ", ".join(filter(None, [data.get("race_name", ""),
                                  race_day.strftime("%d/%m/%Y")]))
    return ('<section class="countdown">'
            f'<p class="countdown__value">{html.esc(headline)}</p>'
            f'<p class="countdown__sub">{html.esc(sub)}</p>'
            "</section>")


def main() -> None:
    args = cli.parse_args(__doc__.splitlines()[0])
    data = cli.read_input_json(MONTH_SCHEMA, args.input)

    weeks = data.get("weeks") or []
    caps = data.get("capabilities") or {}
    scale = float(data["cap_scale"])

    parts = ['<main id="card">', html.header(data["month"], "le mois en un coup d'œil"),
             '<div class="stack">', html.tiles(build_tiles(data)), "</div>"]
    if weeks:
        parts.append(html.section("km par semaine"))
        parts.append(charts.bars(
            [{"label": week.get("label", ""), "value": week.get("km") or 0}
             for week in weeks], height=230, unit="km"))
    if caps:
        parts.append(html.section(f"capacités course (0-{scale:g})"))
        parts.append(capability_tracks(caps, scale))
    parts.append(countdown(data))
    parts.append("</main>")

    render.emit("".join(parts), args.out, html_only=args.html)
    if not args.html:
        archive.write(Path(args.out), kind="monthly", title=data["month"],
                      period=data.get("period") or "")


if __name__ == "__main__":
    entrypoint.run(main)
