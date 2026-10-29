"""The Sunday review dossier, computed by the service BEFORE the model's turn.

Volume, gauge, pain, trajectory, signals, due predictions and deadlines are computed once,
by the same code as the CLIs (`ledger.week_report`, `signals.signals`,
`model.predictions`), and handed to the model as data. The model keeps what is its own:
judging, deciding, writing. It reruns a CLI only to check a specific point.

Not a CLI: no turn calls it, the service imports it (`redline/vault.py`).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redline.analysis import feasibility, ledger, permit_report, signals
from redline.coach.fr import weekly_dossier as text
from redline.model import predictions as prediction_model
from redline.model import program

if TYPE_CHECKING:
    from redline.storage import OperationalStore

#: Beyond this, the dossier summarises instead of listing: it is injected into a prompt.
MAX_LISTED = 12


def _as_date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])


def render(store: OperationalStore, root: Path, week: str, today: dt.date) -> str:
    """The dossier for week `week` (the one that CLOSES), seen from `today`."""
    rows = ledger.parse_ledger(store)
    instruments = ledger.load_instruments(store)
    targets = program.rpe_targets(root)
    lines = [text.HEADER, ""]
    lines.extend(ledger.week_report(store, rows, instruments, targets, week, today,
                                    closing=True))

    weeks = signals._weeks(rows, 4)
    if weeks:
        found = signals.signals(rows, weeks, signals.intentional_cadence_keys(store))
        lines += ["", text.SIGNALS]
        lines.extend(str(signal).strip() for signal in found)
        if not found:
            lines.append(text.NOTHING)

    every = store.prediction_rows()
    typed = [row for row in every
             if row.get("status") == "open" and _as_date(row["due_on"]) <= today]
    lines += ["", text.PREDICTIONS_DUE.format(typed=typed)]
    lines.extend(text.PREDICTION_LINE.format(prediction_key=row["prediction_key"], as_date=_as_date(row["due_on"]), statement=str(row.get("statement", ""))[:160]) for row in typed[:MAX_LISTED])
    calibration = prediction_model.calibration_lines(every, today)
    lines += ["", text.CALIBRATION]
    lines.extend(calibration or [text.NO_PREDICTION])

    overdue = [row for row in store.deadline_rows()
               if row.get("status") != "done" and _as_date(row["due_on"]) < today]
    lines += ["", text.DEADLINES_DUE.format(overdue=overdue)]
    lines.extend(text.DEADLINE_LINE.format(deadline_key=row["deadline_key"], as_date=_as_date(row["due_on"]), item=row["item"]) + (text.DEADLINE_BLOCKS.format(blocks=row["blocks"]) if row.get("blocks") else "")
                 for row in overdue[:MAX_LISTED])

    permit_lines = permit_report.report(store, today, week)
    if permit_lines:
        lines += ["", text.PERMIT]
        lines.extend(permit_lines)

    lines += ["", *feasibility.report(store, rows, today)]

    capabilities = store.latest_capabilities()
    if capabilities:
        lines += ["", text.CAPABILITIES + " · ".join(
            f"{row['slug']} {row['score']}/{row['scale']}" for row in capabilities)]
    lines.append(text.FOOTER)
    return "\n".join(lines)
