"""The "Forme" line of `ai/hot-cache.md`, rendered from the ledger.

Only this line is generated: it is the only derived state in the status block. The rest
is editorial judgement, and generating it would derive prose from prose.

The point is not saving space but preventing drift: figures typed by hand went silently
stale each time a row was added to the ledger.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redline.coach.fr import hot_cache as text
from redline.coach.fr import ledger as ledger_text

from . import program, vault_layout
from .views import apply

if TYPE_CHECKING:
    from redline.storage import OperationalStore

BLOCK = "FORM"
#: The second generated block: the state that drives the coming weeks, recomputed on
#: every projection so that hand-written prose cannot go stale in the file read each turn.
STATE_BLOCK = "STATE"


def target(vault: Path) -> Path:
    return vault / vault_layout.HOT_CACHE


def _round(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def render(ledger, rows: list, instruments: dict, targets: dict,
           today: dt.date | None = None, zones: list | None = None) -> str:
    """The Forme paragraph for the latest week of the ledger.

    `ledger` is the `redline ledger` module, passed in rather than imported:
    `model/` depends on nothing above it, which keeps this layer importable on a machine
    without dependencies.
    """
    per_week = ledger.eq_hours_per_week(rows, instruments, targets)
    if not per_week:
        return text.EMPTY_LEDGER
    # A week whose rows all have zero weight (e.g. a spontaneous reading logged before the
    # first session) has not started yet. Render the latest week with real weight, else
    # (fresh ledger) the most recent one anyway.
    weighted = {label: h for label, h in per_week.items() if h > 0}
    label = max(weighted) if weighted else max(per_week)
    week = [row for row in rows if row.week == label]

    instr = instruments.get(label, ledger.Instruments())
    h_eq, level, _ = ledger.equivalent_hours(week, instr, targets)
    ratio = ledger.acute_chronic_ratio(per_week, label)

    # Volume comes from `ledger.week_volume` only: this line and `redline ledger --week`
    # must report the same mileage by construction, not by coincidence of two sums.
    volume = ledger.week_volume(week)
    run_km, walk_km, runs = volume.km_run, volume.km_walk, volume.runs

    # Walking share of the gauge: it tells whether a red comes from the injury or is
    # merely systemic.
    walk_h = sum(ledger.row_weight(row, targets)[0] for row in week
                 if row.type in ledger.WALK_TYPES)
    base = sum(ledger.row_weight(row, targets)[0] for row in week) or 1.0
    walk_share = round(100 * walk_h / base)

    # Two series, never merged: "during effort" and "at rest" answer different questions
    # and the pain rule needs both.
    def peak(series) -> str:
        return _round(series.peak) if series.n else "—"

    # One trend per monitored zone, never a single one for all.
    trends = [(zone.label, " → ".join(
        f"{w.week.split('-')[-1]} {peak(w.effort)}/{peak(w.rest)}"
        for w in ledger.pain_trend(rows, 3, zone.slug))) for zone in zones or []]
    trend = (" · ".join(f"{label} {text or '—'}" for label, text in trends) if len(trends) > 1
             else (trends[0][1] or "—") if trends else text.NO_ZONE)

    red = []
    if level > ledger.LEVEL_CAP:
        red.append(text.LEVEL)
    if ratio and ratio > ledger.RATIO_CAP:
        red.append("ratio")
    verdict = text.RED.format(join=text.AND.join(red)) if red else ""
    walk_note = (text.WALK_SHARE.format(walk_share=walk_share) if walk_share >= 25 else "")

    short = label.split("-")[-1]          # « 2030-W30 » -> « W30 »
    # A week is closed only once it is over: mid-week the missing days would fake a
    # collapse in volume and ratio.
    day = today or dt.date.today()
    iso = day.isocalendar()
    if label == f"{iso.year}-W{iso.week:02d}":
        # Nothing that changes daily: the view is checked against its regeneration, and a
        # current date would make it drift every day.
        state = text.WEEK_OPEN.format(week=short)
    else:
        state = text.WEEK_CLOSED.format(week=short)
    return (
        text.FORM.format(state=state, run_km=_round(run_km), outings=ledger_text.outings(runs))
        + (text.FORM_WALK.format(walk_km=_round(walk_km)) if walk_km else "")
        + text.FORM_TAIL.format(trend=trend, week=short, h_eq=_round(h_eq), level=level, ratio=_round(ratio) if ratio is not None else "—", verdict=verdict, walk_note=walk_note)
    )


def _short(text: str, limit: int = 60) -> str:
    """Truncate: the block is read every turn and counts against the hot-cache budget."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def render_state(ledger, rows: list, *, deadlines: list[dict], capabilities: list[dict],
                 typed_due: int, race_on: dt.date | None,
                 today: dt.date) -> str:
    """The STATE block: the computed state that drives the coming weeks."""
    # No current date in the output: it changes only with the facts or the week.
    lines = [text.STATE_HEADER]
    trajectory = []
    longest = ledger.longest_recent(rows, today)
    if longest is not None:
        trajectory.append(text.LONGEST.format(km=_round(longest)))
    stall = ledger.long_run_stall(rows, today)
    if stall is not None and stall.weeks:
        flag = text.STALL_PROTOCOL if stall.triggered else ""
        trajectory.append(text.STALL.format(weeks=stall.weeks, flag=flag))
    if race_on is not None:
        weeks = (ledger._week_start(ledger._week_label(race_on))
                 - ledger._week_start(ledger._week_label(today))).days // 7
        trajectory.append(text.RACE.format(race_on=race_on, weeks=weeks))
    if trajectory:
        lines.append(text.TRAJECTORY + " · ".join(trajectory) + ".")
    overdue = [row for row in deadlines
               if row.get("status") != "done" and _as_date(row["due_on"]) < today]
    if overdue:
        shown = " · ".join(f"{row['deadline_key']} ({_as_date(row['due_on']):%d/%m})"
                           + (text.DEADLINE_BLOCKS.format(blocks=_short(row["blocks"])) if row.get("blocks") else "")
                           for row in overdue[:4])
        more = f" · +{len(overdue) - 4}" if len(overdue) > 4 else ""
        lines.append(text.OVERDUE.format(shown=shown, more=more))
    if capabilities:
        lines.append(text.CAPABILITIES + " · ".join(
            f"{row['slug']} {row['score']}/{row['scale']}" for row in capabilities) + ".")
    if typed_due:
        lines.append(text.PREDICTIONS_DUE.format(typed_due=typed_due))
    return "\n".join(lines)


def state_inputs(store: OperationalStore, vault: Path, today: dt.date) -> dict:
    """Read what the ÉTAT block displays, from each owner."""
    predictions = store.prediction_rows()
    typed_due = sum(1 for row in predictions if row.get("status") == "open"
                    and _as_date(row["due_on"]) <= today)
    deadlines = store.deadline_rows()
    races = [_as_date(row["due_on"]) for row in deadlines
             if row.get("kind") == "race" and row.get("status") != "done"
             and _as_date(row["due_on"]) > today]
    return {"deadlines": deadlines, "capabilities": store.latest_capabilities(),
            "typed_due": typed_due,
            "race_on": min(races) if races else None}


def refresh(vault: Path, ledger, rows: list, instruments: dict,
            *, store: Any = None, check: bool = False,
            today: dt.date | None = None) -> bool:
    """True if a block had drifted. With `check`, nothing is written.

    RPE targets are resolved HERE, from the vault: the Forme block must render exactly the
    same gauge as `redline ledger --week`, and a second source of targets would let them
    diverge silently. The ÉTAT block is rendered only if the file carries its markers and
    a store is provided.
    """
    day = today or dt.date.today()
    targets = program.rpe_targets(vault)
    zones = ledger.pain_zones(store) if store is not None else []
    drifted = apply(target(vault), BLOCK,
                    render(ledger, rows, instruments, targets, today=day, zones=zones),
                    check=check)
    path = target(vault)
    if store is not None and f"<!-- {STATE_BLOCK}_START -->" in path.read_text(
            encoding="utf-8"):
        state = render_state(ledger, rows, today=day, **state_inputs(store, vault, day))
        drifted = apply(path, STATE_BLOCK, state, check=check) or drifted
    return drifted
