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

from . import programme, vault_layout
from .views import apply

if TYPE_CHECKING:
    from redline.storage import OperationalStore

BLOCK = "FORME"
#: The second generated block: the state that drives the coming weeks, recomputed on
#: every projection so that hand-written prose cannot go stale in the file read each turn.
STATE_BLOCK = "ETAT"


def target(vault: Path) -> Path:
    return vault / vault_layout.HOT_CACHE


def _round(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def render(ledger, rows: list, instruments: dict, targets: dict,
           today: dt.date | None = None, zones: list | None = None) -> str:
    """The Forme paragraph for the latest week of the ledger.

    `ledger` is the `scripts/ledger.py` module, passed in rather than imported:
    `model/` depends on nothing above it, which keeps this layer importable on a machine
    without dependencies.
    """
    per_week = ledger.eq_hours_per_week(rows, instruments, targets)
    if not per_week:
        return "- **Forme** : registre vide."
    # A week whose rows all have zero weight (e.g. a spontaneous reading logged before the
    # first session) has not started yet. Render the latest week with real weight, else
    # (fresh ledger) the most recent one anyway.
    weighted = {label: h for label, h in per_week.items() if h > 0}
    label = max(weighted) if weighted else max(per_week)
    week = [row for row in rows if row.week == label]

    instr = instruments.get(label, ledger.Instruments())
    h_eq, level, _ = ledger.equivalent_hours(week, instr, targets)
    ratio = ledger.acute_chronic_ratio(per_week, label)

    # Volume comes from `ledger.week_volume` only: this line and `ledger.py --semaine`
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
             else (trends[0][1] or "—") if trends else "aucune zone surveillée")

    red = []
    if level > ledger.LEVEL_CAP:
        red.append("niveau")
    if ratio and ratio > ledger.RATIO_CAP:
        red.append("ratio")
    verdict = f" — **{' et '.join(red)} au rouge**" if red else ""
    walk_note = (f", dont **{walk_share} % de marche**" if walk_share >= 25 else "")

    short = label.split("-")[-1]          # « 2026-W30 » -> « W30 »
    sorties = f"{runs} sortie" + ("s" if runs > 1 else "")
    # A week is "bouclée" only once it is CLOSED: mid-week the missing days would fake a
    # collapse in volume and ratio.
    day = today or dt.date.today()
    iso = day.isocalendar()
    if label == f"{iso.year}-W{iso.week:02d}":
        # Nothing that changes daily: the view is checked against its regeneration, and a
        # current date would make it drift every day.
        state = f"{short} en cours, semaine ouverte, ratio partiel"
    else:
        state = f"{short} bouclée"
    return (
        f"- **Forme** : **{state}** — {_round(run_km)} km courus ({sorties})"
        + (f" + {_round(walk_km)} km marchés" if walk_km else "")
        + f". **Tendance douleur, au PIC hebdo** (jamais la médiane), "
        f"**effort/repos** : {trend}. "
        f"**Jauge : elle se CALCULE, ne la réécris jamais de tête** — "
        f"`python3 scripts/ledger.py --semaine <W>`. {short} = "
        f"**{_round(h_eq)} h-éq · {level}/100 · ratio "
        f"{_round(ratio) if ratio is not None else '—'}**{verdict}{walk_note}. "
        f"Faits : PostgreSQL. Bloc **généré** par `scripts/hot-cache.py`."
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
    """The ÉTAT block: the computed state that drives the coming weeks."""
    # No current date in the output: it changes only with the facts or the week.
    lines = ["- **État calculé** depuis PostgreSQL et decisions.md par "
             "`scripts/hot-cache.py`, ne pas l'éditer."]
    trajectory = []
    longest = ledger.longest_recent(rows, today)
    if longest is not None:
        trajectory.append(f"plus longue des 30 j **{_round(longest)} km**")
    stall = ledger.long_run_stall(rows, today)
    if stall is not None and stall.weeks:
        flag = " 🔴 protocole des baisses ouvert (s9)" if stall.triggered else ""
        trajectory.append(f"sans nouveau record depuis {stall.weeks} semaine(s) close(s){flag}")
    if race_on is not None:
        weeks = (ledger._week_start(ledger._week_label(race_on))
                 - ledger._week_start(ledger._week_label(today))).days // 7
        trajectory.append(f"course le {race_on:%d/%m}, dans {weeks} semaine(s)")
    if trajectory:
        lines.append("- **Trajectoire** : " + " · ".join(trajectory) + ".")
    overdue = [row for row in deadlines
               if row.get("status") != "done" and _as_date(row["due_on"]) < today]
    if overdue:
        shown = " · ".join(f"{row['deadline_key']} ({_as_date(row['due_on']):%d/%m})"
                           + (f", bloque {_short(row['blocks'])}" if row.get("blocks") else "")
                           for row in overdue[:4])
        more = f" · +{len(overdue) - 4}" if len(overdue) > 4 else ""
        lines.append(f"- **Échéances échues** : {shown}{more}.")
    if capabilities:
        lines.append("- **Capacités** : " + " · ".join(
            f"{row['slug']} {row['score']}/{row['scale']}" for row in capabilities) + ".")
    if typed_due:
        lines.append(f"- **Prédictions à solder** : {typed_due} "
                     "(`fact-read.py predictions --due`).")
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
    same gauge as `ledger.py --semaine`, and a second source of targets would let them
    diverge silently. The ÉTAT block is rendered only if the file carries its markers and
    a store is provided.
    """
    day = today or dt.date.today()
    targets = programme.rpe_targets(vault)
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
