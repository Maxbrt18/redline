"""The Sunday review dossier, computed by the service BEFORE the model's turn.

Volume, gauge, pain, trajectory, signals, due predictions, the coach's cautious decisions
with what followed, deadlines and the strength progression proposals are computed once, by the same code as the CLIs
(`week_review.week_report`, `signals.signals`, `model.predictions`,
`progression_strength.report`), and handed to the model as data. The model keeps what is
its own: judging, deciding, writing. It reruns a CLI only to check a specific point.

Not a CLI: no turn calls it, the service imports it (`service/vault.py`).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

from redline.analysis import (
    caution_review,
    cycle_report,
    expected_rpe,
    feasibility,
    ledger,
    permit_report,
    progression_strength,
    signals,
    week_review,
    weekly_draft,
)
from redline.coach import dates
from redline.coach.fr import debrief as debrief_text
from redline.coach.fr import progression as progression_text
from redline.coach.fr import weekly_dossier as text
from redline.model import iso_week
from redline.model import predictions as prediction_model
from redline.model.errors import RedlineError
from redline.model.vocabulary import DeadlineStatus

if TYPE_CHECKING:
    from redline.storage import OperationalStore

#: Beyond this, the dossier summarises instead of listing: it is injected into a prompt.
MAX_LISTED = 12


def _cycle(store: OperationalStore, week: str, today: dt.date) -> list[str]:
    """The cycle in force, the week the review prepares within it and the engine's draft
    of that week; a refusal is reported."""
    try:
        lines = ["", *cycle_report.review_lines(store, week, today)]
    except RedlineError as exc:
        return ["", text.CYCLE_UNAVAILABLE.format(reason=str(exc).replace("\n", " "))]
    upcoming = iso_week.shift(week, 1)
    try:
        found = weekly_draft.frame(store, upcoming, today)
    except RedlineError as exc:
        return [*lines, text.DRAFT_UNAVAILABLE.format(reason=str(exc).replace("\n", " "))]
    if found is None:
        return lines
    draft = weekly_draft.draft(found, f"{today.isoformat()}T00:00:00+00:00")
    return [*lines, "", text.DRAFT.format(week=upcoming,
                                          volume=draft.get("volume_target_km") or 0),
            *(text.DRAFT_SESSION.format(day=dates.weekday(dt.date.fromisoformat(session["date"])),
                                        session=session["id"], title=session["title"],
                                        km=session.get("distance_target_km") or 0,
                                        key=text.DRAFT_KEY.format(position=session["key_position"])
                                        if session.get("key_position") is not None else "")
              for session in draft["sessions"])]


def _progression(store: OperationalStore, root: Path, week: str, today: dt.date) -> list[str]:
    """The strength proposals for the week the review prepares; a refusal is reported."""
    try:
        return progression_strength.report(store, root, today, iso_week.shift(week, 1))
    except RedlineError as exc:
        return [progression_text.UNAVAILABLE.format(reason=str(exc).replace("\n", " "))]


def _effects(store: OperationalStore, rows: list, today: dt.date) -> list[str]:
    """What heat, night and group add to the RPE, as calibrated today."""
    config = expected_rpe.settings()
    if config is None:
        return []
    effects = expected_rpe.history(store, rows, config).effects(today)
    return ["", text.EFFECTS.format(effects=" ; ".join(
        text.EFFECT.format(
            condition=debrief_text.CONDITIONS[effect.condition],
            value=debrief_text.signed(round(effect.value, 2)),
            unit=text.EFFECT_UNITS[effect.condition].format(
                threshold=expected_rpe.HEAT_FROM_WBGT_C),
            sessions=effect.sessions, state=text.EFFECT_STATES[effect.supported])
        for effect in effects))]


def render(store: OperationalStore, root: Path, week: str, today: dt.date) -> str:
    """The dossier for week `week` (the one that CLOSES), seen from `today`."""
    rows = ledger.parse_ledger(store)
    instruments = ledger.load_instruments(store)
    above = expected_rpe.above_range(store, rows)
    lines = [text.HEADER, ""]
    lines.extend(week_review.week_report(store, rows, instruments, above, week, today,
                                    closing=True))

    weeks = signals.recent_weeks(rows, 4)
    if weeks:
        found = signals.signals(rows, weeks, signals.intentional_cadence_keys(store))
        lines += ["", text.SIGNALS]
        lines.extend(str(signal).strip() for signal in found)
        if not found:
            lines.append(text.NOTHING)

    every = store.prediction_rows()
    typed = [row for row in every
             if row.get("status") == "open" and row["due_on"] <= today]
    lines += ["", text.PREDICTIONS_DUE.format(typed=len(typed))]
    lines.extend(text.PREDICTION_LINE.format(prediction_key=row["prediction_key"], as_date=row["due_on"], statement=str(row.get("statement", ""))[:160]) for row in typed[:MAX_LISTED])
    calibration = prediction_model.calibration_lines(every, today)
    lines += ["", text.CALIBRATION]
    lines.extend(calibration or [text.NO_PREDICTION])
    lines += _effects(store, rows, today)
    cautious = caution_review.report(store, rows, week)
    if cautious:
        lines += ["", *cautious]

    overdue = [row for row in store.deadline_rows()
               if row.get("status") != DeadlineStatus.DONE and row["due_on"] < today]
    lines += ["", text.DEADLINES_DUE.format(overdue=len(overdue))]
    lines.extend(text.DEADLINE_LINE.format(deadline_key=row["deadline_key"], as_date=row["due_on"], item=row["item"]) + (text.DEADLINE_BLOCKS.format(blocks=row["blocks"]) if row.get("blocks") else "")
                 for row in overdue[:MAX_LISTED])

    permit_lines = permit_report.report(store, today, week)
    if permit_lines:
        lines += ["", text.PERMIT]
        lines.extend(permit_lines)

    lines += ["", *feasibility.report(store, rows, today)]
    lines += _cycle(store, week, today)
    lines += ["", *_progression(store, root, week, today)]

    capabilities = store.latest_capabilities()
    if capabilities:
        lines += ["", text.CAPABILITIES + " · ".join(
            f"{row['slug']} {row['score']}/{row['scale']}" for row in capabilities)]
    lines.append(text.FOOTER)
    return "\n".join(lines)
