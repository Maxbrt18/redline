#!/usr/bin/env python3
"""Score the whole-session dose against the WER load of the repetitions on the athlete's
quality runs: which one predicts their declared RPE better, left out one at a time.

Read-only. Turn `rpe_quality_dose_reps` on in the rulebook only once the WER load wins on
enough runs.
"""

from __future__ import annotations

import argparse

from redline.analysis import expected_rpe, ledger, rep_dose
from redline.evaluation import quality_dose
from redline.model import entrypoint
from redline.model.errors import ConfigError


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    from redline.storage import open_store

    config = expected_rpe.settings()
    if config is None:
        raise ConfigError("rulebook.toml declares no expected-RPE thresholds")
    store = open_store("evaluate-quality-dose")
    rows = ledger.parse_ledger(store)
    history = expected_rpe.history(store, rows, config)

    def whole(row: ledger.Row) -> float | None:
        found = history.dose(row)
        return None if found is None else found.value

    scores = quality_dose.score(rows, whole, rep_dose.quality_doses(store), config)
    for item in scores:
        print(f"{item.kind:12} runs {item.runs:3}  paired {item.paired:3}  "
              f"session MAE {_number(item.session_mae)}  reps MAE {_number(item.reps_mae)}")
    if not scores:
        print("no quality run with a declared RPE")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
