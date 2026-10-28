"""Calibration des prédictions typées, par domaine, depuis leurs lignes PostgreSQL.

Audit du 2026-10-09 : le score de Brier seul cachait ce qui comptait. Sur le corps,
7 prédictions confrontées sur 20 ; une moyenne calculée sur les 7 qu'on a bien voulu
solder ne mesure pas la calibration, elle mesure ce qu'on a choisi de regarder. La
ligne montre donc aussi ce qui a échappé au score : `void` et échues encore ouvertes,
et combien de soldes sont venus d'une observation plutôt que d'un verdict rédigé.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from scripts.coach.fr import messages

#: Why a prediction was refuted: the decision did worse than predicted, or better.
REFUTED_REASONS = ("optimistic", "cautious")
#: Why it could not be tested: no measure, the situation never came, the criterion
#: needed an exhaustive inventory, or a later decision withdrew it before its review.
VOID_REASONS = ("instrument", "exposure", "scope", "superseded")
OUTCOME_REASON_RULE = (
    "outcome_reason IS NULL OR "
    "(status = 'resolved' AND outcome = false AND outcome_reason IN "
    f"({', '.join(repr(reason) for reason in REFUTED_REASONS)})) OR "
    "(status = 'void' AND outcome_reason IN "
    f"({', '.join(repr(reason) for reason in VOID_REASONS)}))")


def _day(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def calibration(rows: Iterable[Mapping[str, Any]], today: dt.date) -> list[dict]:
    """One line per domain: settled, of which by observation, void, overdue, Brier, and
    how many carry each outcome reason."""
    domains: dict[str, dict] = defaultdict(lambda: {
        "resolved": 0, "observed": 0, "void": 0, "overdue": 0, "scores": [],
        "reasons": Counter()})
    for row in rows:
        entry = domains[row["domain"]]
        if row.get("outcome_reason"):
            entry["reasons"][row["outcome_reason"]] += 1
        if row["status"] == "resolved":
            entry["resolved"] += 1
            entry["scores"].append(float(row["brier_score"]))
            entry["observed"] += bool(row.get("resolves_kind"))
        elif row["status"] == "void":
            entry["void"] += 1
        elif _day(row["due_on"]) < today:
            entry["overdue"] += 1
    return [{"domain": domain, "resolved": entry["resolved"], "observed": entry["observed"],
             "void": entry["void"], "overdue": entry["overdue"],
             "brier": sum(entry["scores"]) / len(entry["scores"]) if entry["scores"] else None,
             "reasons": dict(entry["reasons"])}
            for domain, entry in sorted(domains.items())]


def calibration_lines(rows: Iterable[Mapping[str, Any]], today: dt.date) -> list[str]:
    lines = []
    for entry in calibration(rows, today):
        brier = "—" if entry["brier"] is None else f"{entry['brier']:.3f}"
        reasons = " · ".join(f"{messages.PREDICTION_REASONS[reason]} {count}"
                             for reason, count in sorted(entry["reasons"].items()))
        lines.append(
            f"Calibration {entry['domain']} · soldées {entry['resolved']} "
            f"(dont {entry['observed']} par observation) · void {entry['void']} · "
            f"échues ouvertes {entry['overdue']} · Brier moyen {brier}"
            + (f" · motifs : {reasons}" if reasons else ""))
    return lines
