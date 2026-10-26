#!/usr/bin/env python3
"""Workout card -- every rep of a quality session as a ribbon in relief.

Form (l'athlète, 2026-10-01, after a dot plot and a speed bar chart he found
unreadable): the session in the order it was run, each block as wide as it
lasted. A rep rises with how much faster than R1 it went -- the shape of the
session, flat or progressive -- and carries its pace on top and its gap to R1
inside, signed like a split (−3 s = faster). No verdict: since 2026-10-26 a
quality is judged on its RPE band and its reserve, which the card does not see,
and paces only compare within one session. Recoveries are pale slivers with their duration, drawn only when the payload
carries them (`recovery` on the rep it follows). Under the ribbon, the heart
rate of each rep, when all of them carry one: what each acceleration cost.
The target pace is said in the subtitle.

Input: WORKOUT_SCHEMA in scripts/model/cards.py, from --input (stdin outside a turn).

Usage:  python3 scripts/chart-workout.py -o server/outbox/workout-123.png < data.json
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cards import charts, cli, html, render  # noqa: E402
from scripts.cards.format import fmt_pace, parse_pace  # noqa: E402
from scripts.model.cards import WORKOUT_SCHEMA  # noqa: E402


def main() -> None:
    args = cli.parse_args(__doc__.splitlines()[0])
    data = cli.read_input_json(WORKOUT_SCHEMA, args.input)

    reps = data["reps"]
    paces = [rep["time_s"] / (rep["dist_m"] / 1000.0) for rep in reps]
    target = parse_pace(data["target_pace"]) if data.get("target_pace") else None
    # Gaps are taken between the paces AS PRINTED, so the card never shows
    # 3:48 and 3:45 with "−2 s" between them.
    r1 = round(paces[0])

    segments = []
    for i, (rep, pace) in enumerate(zip(reps, paces, strict=True)):
        gap = None if i == 0 else round(pace) - r1
        segments.append({"kind": "rep", "time_s": rep["time_s"], "pace": pace, "gap": gap,
                         "label": rep.get("label") or f"R{i + 1}",
                         "hr": rep.get("hr")})
        recovery = rep.get("recovery")
        if recovery:
            segments.append({"kind": "rec", "time_s": recovery["time_s"]})

    legend = [{"kind": "effort", "label": "répétition, plus haut = plus vite que R1"}]
    if any(seg["kind"] == "rec" for seg in segments):
        legend.append({"kind": "ghost", "label": "récup"})

    subtitle = ", ".join(filter(None, [data.get("date", ""),
                                       f"cible {fmt_pace(target)}" if target else "",
                                       data.get("note", "")]))
    body = (
        '<main id="card">'
        + html.header(data.get("title", "Séance"), subtitle)
        + html.legend(legend)
        + charts.ribbon(segments)
        + "</main>"
    )
    render.emit(body, args.out, html_only=args.html)


if __name__ == "__main__":
    main()
