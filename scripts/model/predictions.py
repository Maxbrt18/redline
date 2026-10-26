"""Calibration des prédictions typées, par domaine, depuis leurs lignes PostgreSQL.

Audit du 2026-10-09 : le score de Brier seul cachait ce qui comptait. Sur le corps,
7 prédictions confrontées sur 20 ; une moyenne calculée sur les 7 qu'on a bien voulu
solder ne mesure pas la calibration, elle mesure ce qu'on a choisi de regarder. La
ligne montre donc aussi ce qui a échappé au score : `void` et échues encore ouvertes,
et combien de soldes sont venus d'une observation plutôt que d'un verdict rédigé.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def _day(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def calibration(rows: Iterable[Mapping[str, Any]], today: dt.date) -> list[dict]:
    """Une ligne par domaine : soldées, dont par observation, void, échues ouvertes, Brier."""
    domains: dict[str, dict] = defaultdict(lambda: {
        "resolved": 0, "observed": 0, "void": 0, "overdue": 0, "scores": []})
    for row in rows:
        entry = domains[row["domain"]]
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
             "brier": sum(entry["scores"]) / len(entry["scores"]) if entry["scores"] else None}
            for domain, entry in sorted(domains.items())]


def calibration_lines(rows: Iterable[Mapping[str, Any]], today: dt.date) -> list[str]:
    lines = []
    for entry in calibration(rows, today):
        brier = "—" if entry["brier"] is None else f"{entry['brier']:.3f}"
        lines.append(
            f"Calibration {entry['domain']} · soldées {entry['resolved']} "
            f"(dont {entry['observed']} par observation) · void {entry['void']} · "
            f"échues ouvertes {entry['overdue']} · Brier moyen {brier}")
    return lines
