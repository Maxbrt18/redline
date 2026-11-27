"""A calibration of the athlete's fitness: a decision adopted on declared evidence.

It carries the references intensities are rendered against: the critical speed and D′
of the short all-out efforts, the filtered VDOT of the longer ones, and the heart-rate
thresholds once measured. A calibration is adopted, dated and kept
(`fitness_calibrations`); the next one is a new calibration, so paces never drift with
each session. The one in force is the latest adopted.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .vocabulary import CalibrationConfidence, CalibrationSource, Decider


@dataclass(frozen=True)
class Calibration:
    adopted_on: dt.date
    confidence: CalibrationConfidence
    source: CalibrationSource
    #: The keys of the all-out efforts it is adopted on.
    evidence: tuple[str, ...]
    decided_by: Decider
    cs_mps: float | None = None
    d_prime_m: float | None = None
    #: Standard error of CS in m/s; None with two efforts, which leave no residual.
    cs_se_mps: float | None = None
    vdot: float | None = None
    vdot_sd: float | None = None
    hr_first_threshold_bpm: int | None = None
    hr_second_threshold_bpm: int | None = None


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _whole(value: Any) -> int | None:
    return None if value is None else int(value)


def from_row(row: Mapping[str, Any]) -> Calibration:
    return Calibration(
        row["adopted_on"], CalibrationConfidence(row["confidence"]),
        CalibrationSource(row["source"]), tuple(row["evidence"]), Decider(row["decided_by"]),
        _number(row["cs_mps"]), _number(row["d_prime_m"]), _number(row["cs_se_mps"]),
        _number(row["vdot"]),
        _number(row["vdot_sd"]), _whole(row["hr_first_threshold_bpm"]),
        _whole(row["hr_second_threshold_bpm"]))


def in_force(rows: Iterable[Mapping[str, Any]]) -> Calibration | None:
    """The latest calibration adopted, from rows in adoption order; None before the first."""
    found = list(rows)
    return from_row(found[-1]) if found else None
