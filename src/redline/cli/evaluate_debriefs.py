#!/usr/bin/env python3
"""Rule the debrief bench's fictional runs and count unjustified reproaches and missed
interventions. Deterministic: no model, no production state.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from redline.evaluation import debrief_bench
from redline.model import entrypoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=debrief_bench.CASES,
                        help="the bench's cases, as JSON")
    args = parser.parse_args()
    outcomes = debrief_bench.run(args.cases)
    for item in outcomes:
        detail = "; ".join(f"{label}: {', '.join(kinds)}" for label, kinds in (
            ("unjustified", item.unjustified), ("missed", item.missed), ("other", item.other))
            if kinds)
        verdict = "" if item.verdict_matches else "verdict differs"
        status = "ok" if item.passed else "FAIL"
        print(f"{status:4} {item.case_id}  {'; '.join(filter(None, (verdict, detail)))}")
    unjustified = sum(len(item.unjustified) for item in outcomes)
    missed = sum(len(item.missed) for item in outcomes)
    print(f"{len(outcomes)} cases, {sum(item.passed for item in outcomes)} passed, "
          f"{unjustified} unjustified reproaches, {missed} missed interventions")
    return 0 if all(item.passed for item in outcomes) else 1


if __name__ == "__main__":
    entrypoint.run(main)
