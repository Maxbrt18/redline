#!/usr/bin/env python3
"""Weekly slate -- planned vs done day by day + the review instruments.

Form: one thin column per day (km done, zero baseline, honest), standing in a
pale, wider column for the planned volume -- under-done leaves pale room above,
over-done stands out of it. Demanding days say "qualité" under the day name. The
review instruments (volume, load gauge, shins, enjoyment, CMJ) sit in a strip of
figures; one that crosses its limit turns red.

Input: WEEK_SCHEMA in src/redline/model/cards.py, from --input (stdin outside a turn).

Usage:  python3 scripts/chart-week.py -o server/outbox/week-29.png < data.json
"""

from pathlib import Path

from redline.cards import archive, charts, cli, html, render
from redline.cards.format import fmt_num
from redline.model import entrypoint
from redline.model.cards import WEEK_SCHEMA

GAUGE_CAP = 85       # coaching/protocols.md
RATIO_CAP = 1.3


def build_tiles(data: dict) -> list[dict]:
    """The review instruments. Long values wrap inside their tile, so `pain` takes
    whatever text it needs."""
    tiles: list[dict] = []
    done, planned = data.get("done_km"), data.get("planned_km")
    if done is not None:
        tiles.append({"label": "Volume", "value": f"{fmt_num(done)} km",
                      "sub": f"prévu {fmt_num(planned)} km" if planned is not None else ""})
    if data.get("gauge_level") is not None:
        level, ratio = data["gauge_level"], data.get("gauge_ratio")
        over = level > GAUGE_CAP or (ratio or 0) > RATIO_CAP
        tiles.append({"label": "Jauge", "value": f"{fmt_num(level)} / {GAUGE_CAP}",
                      "sub": f"ratio {fmt_num(round(ratio, 2))} / 1,3" if ratio is not None else "",
                      "status": "bad" if over else "ok"})
    if data.get("pain") is not None:
        tiles.append({"label": "Injury", "value": str(data["pain"]),
                      "status": data.get("pain_flag"), "kind": "text"})
    if data.get("plaisir") is not None:
        tiles.append({"label": "Plaisir", "value": f"{fmt_num(data['plaisir'])} / 10"})
    if data.get("cmj_cm") is not None:
        tiles.append({"label": "CMJ", "value": f"{fmt_num(data['cmj_cm'])} cm", "sub": "lundi"})
    return tiles


def main() -> None:
    args = cli.parse_args(__doc__.splitlines()[0])
    data = cli.read_input_json(WEEK_SCHEMA, args.input)

    columns = [{"label": day.get("day", ""), "value": day.get("done_km") or 0,
                "planned": day.get("planned_km") or 0,
                "note": "qualité" if day.get("quality") else ""}
               for day in data["days"]]

    body = (
        '<main id="card">'
        + html.header(f"Semaine {data['week']}".strip(), data.get("dates", ""))
        + html.legend([{"kind": "data", "label": "réalisé"},
                       {"kind": "ghost", "label": "prévu"}])
        + charts.bars(columns, height=300, unit="km")
        + html.section("les instruments de la semaine")
        + html.tiles(build_tiles(data))
        + "</main>"
    )
    render.emit(body, args.out, html_only=args.html)
    if not args.html:
        archive.write(Path(args.out), kind="weekly", title="Weekly · " + data["week"],
                      period=data.get("period") or "")


if __name__ == "__main__":
    entrypoint.run(main)
