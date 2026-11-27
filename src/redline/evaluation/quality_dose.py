"""Which dose predicts the athlete's RPE on quality runs better: the whole session's or
the WER load of its repetitions (`redline.analysis.rep_dose`).

Each quality run with a declared RPE and both doses is predicted from the other runs of
its kind, leaving it out, by the expected RPE's model, RPE = a + b × ln(dose). Both doses
are scored on the same runs: the mean absolute error of each, and how many runs a kind
loses because one dose is missing. Read-only, on the athlete's own history.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from redline.analysis import expected_rpe, rep_dose
from redline.analysis.ledger import Row
from redline.model import session_kinds


@dataclass(frozen=True)
class Score:
    kind: str
    #: Quality runs of the kind with a declared RPE.
    runs: int
    #: Those with both doses, the runs both are scored on.
    paired: int
    session_mae: float | None
    reps_mae: float | None


def _mae(points: list[tuple[float, float]], config: expected_rpe.Settings) -> float | None:
    errors = rep_dose.leave_one_out(points, lambda others: expected_rpe.fit(others, config))
    return statistics.fmean(errors) if errors else None


def score(rows: Iterable[Row], session_dose: Callable[[Row], float | None],
          reps_dose: Callable[[Row], float | None],
          config: expected_rpe.Settings) -> list[Score]:
    """Each quality kind's leave-one-out error under both doses, on the same runs."""
    out = []
    by_kind: dict[str, list[Row]] = {}
    for row in rows:
        if row.type in session_kinds.QUALITY and row.rpe is not None:
            by_kind.setdefault(row.type, []).append(row)
    for kind, own in sorted(by_kind.items()):
        pairs = [(whole, reps, row.rpe) for row in own
                 if (whole := session_dose(row)) and (reps := reps_dose(row))
                 and row.rpe is not None]
        out.append(Score(kind, len(own), len(pairs),
                         _mae([(whole, rpe) for whole, _, rpe in pairs], config),
                         _mae([(reps, rpe) for _, reps, rpe in pairs], config)))
    return out
