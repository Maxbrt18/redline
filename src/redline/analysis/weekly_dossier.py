"""The Sunday review dossier, computed by the service BEFORE the model's turn.

Volume, gauge, pain, trajectory, signals, due predictions, deadlines and the strength
progression proposals are computed once, by the same code as the CLIs
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
    feasibility,
    ledger,
    permit_report,
    progression_strength,
    signals,
    week_review,
)
from redline.coach.fr import progression as progression_text
from redline.coach.fr import weekly_dossier as text
from redline.model import iso_week, program
from redline.model import predictions as prediction_model
from redline.model.errors import RedlineError
from redline.model.vocabulary import DeadlineStatus

if TYPE_CHECKING:
    from redline.storage import OperationalStore

#: Beyond this, the dossier summarises instead of listing: it is injected into a prompt.
MAX_LISTED = 12


def _progression(store: OperationalStore, root: Path, week: str, today: dt.date) -> list[str]:
    """The strength proposals for the week the review prepares; a refusal is reported."""
    try:
        return progression_strength.report(store, root, today, iso_week.shift(week, 1))
    except RedlineError as exc:
        return [progression_text.UNAVAILABLE.format(reason=str(exc).replace("\n", " "))]


def render(store: OperationalStore, root: Path, week: str, today: dt.date) -> str:
    """The dossier for week `week` (the one that CLOSES), seen from `today`."""
    rows = ledger.parse_ledger(store)
    instruments = ledger.load_instruments(store)
    targets = program.rpe_targets(root)
    lines = [text.HEADER, ""]
    lines.extend(week_review.week_report(store, rows, instruments, targets, week, today,
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
    lines += ["", *_progression(store, root, week, today)]

    capabilities = store.latest_capabilities()
    if capabilities:
        lines += ["", text.CAPABILITIES + " · ".join(
            f"{row['slug']} {row['score']}/{row['scale']}" for row in capabilities)]
    lines.append(text.FOOTER)
    return "\n".join(lines)
