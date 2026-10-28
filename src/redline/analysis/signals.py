#!/usr/bin/env python3
"""Weak signals, computed: the half of section D of `coaching/protocols.md` that
can be read from the ledger.

`protocols.md` lists five checks "that cannot lie". None was **computed**: they relied on
the coach remembering them when rereading on a Sunday. This is the mechanism meant to catch
the injury before the athlete admits it.

**What this script does NOT do, and why.** Four of the checks in section D live in Strava,
not in the ledger: downhill pace on reference segments, shortened routes, unusual pauses at
the end of a run, note length. They stay with the coach at the weekly review, via
`scripts/strava.py`: this script only reads the ledger.

**The thresholds are coarse, on purpose.** They need not be right: they must force a look.
A signal that comes out here is never a verdict, it is a line to go and check, the same
doctrine as the gauge: "warning signal, never a guillotine".

    python3 scripts/signals.py            # the last 4 weeks
    python3 scripts/signals.py --semaines 6
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
from dataclasses import dataclass
from pathlib import Path

from redline.analysis import ledger as gauge
from redline.model import (
    paths,
    session_kinds,
)

# A cadence that RISES at equal pace deserves a look. It does not carry its cause:
# deliberate work, terrain, pace, measurement noise and protection are all hypotheses.
# In steps/min, both feet: 4 is above the noise of a session average.
CADENCE_JUMP = 4.0
# The runs between which cadence is comparable. Elsewhere (hills, trail, D+, quality
# work) it varies on its own and measuring it there would read the terrain, not the injury.
EASY_TYPES = session_kinds.EASY_RUNS
# The longest run is counted over everything RUN: capping it to easy/long runs only would
# make a group run or a race look like a calm week, and that is exactly the kind of run
# that makes the load spike.
RUN_TYPES = session_kinds.RUN
# Enjoyment is no longer a signal: the claim that it drops one or two runs before pain is
# admitted had no source and was never verified in the ledger.
# The longest run has NO jump signal any more: no fixed percentage judges its progression
# (see `src/redline/model/rulebook.py`). It is still displayed, and its STAGNATION is
# still reported (scenario 9).

# COMPUTED ALARM no. 4. "Abnormally high HR on an easy run" is the marker that preceded the
# earlier collapse by one to two weeks; it was computed nowhere. A flat easy run is
# compared to its neighbours at the same PACE, never to a global average: otherwise we
# would be measuring pace.
HR_DRIFT_BPM = 6            # above the noise of a session-average HR
HR_PACE_BAND_S = 20         # "same pace": ±20 s/km
HR_BASELINE_DAYS = 42       # six reference weeks, before the run
HR_BASELINE_MIN = 3         # below this, no reference, hence no verdict
HR_FLAT_M_PER_KM = 15       # above this, the relief drives HR
HR_MIN_KM = 4.0
# Weather normalises HR, it does not excuse it: a hot day enters neither the reference nor
# the comparison. Unknown WBGT: kept.
HR_HOT_WBGT_C = 22.0


@dataclass
class Signal:
    label: str
    detail: str

    def __str__(self) -> str:
        return f"  ⚑ {self.label} — {self.detail}"


def _weeks(rows: list, count: int) -> list:
    """The last `count` week labels present in the ledger, in order."""
    return sorted({row.week for row in rows})[-count:]


def intentional_cadence_keys(store: gauge.LedgerStore) -> frozenset[str]:
    """The runs whose cadence was worked on purpose, prescribed or voluntary.

    A deliberate cadence increase is not a signal. Every reader of the signals goes
    through here, so requested work is never flagged.
    """
    exposures = store.exposure_rows(confirmed_only=True)
    return frozenset(
        row["observation_key"] for row in exposures
        if row["exposure_type"] == "cadence"
        and row["origin"] in {"prescribed", "voluntary"})


def current(store: gauge.LedgerStore, count: int = 4) -> list:
    """The signals of the last `count` ledger weeks, as every reader sees them."""
    rows = gauge.parse_ledger(store)
    weeks = _weeks(rows, count)
    return signals(rows, weeks, intentional_cadence_keys(store)) if weeks else []


def _mean(values: list) -> float | None:
    return sum(values) / len(values) if values else None


def cadence_at_easy_pace(rows: list, weeks: list, intentional_keys: frozenset[str]) -> list:
    """Mean cadence on easy runs, week by week, at comparable pace.

    Only easy runs are compared with each other, otherwise we mostly measure the
    terrain. Even when comparable, a rise is an observation, never its explanation.
    """
    out = []
    for label in weeks:
        cadences = [row.cadence for row in rows
                    if row.week == label and row.type in EASY_TYPES and row.cadence
                    and row.idempotency_key not in intentional_keys]
        out.append((label, _mean([float(c) for c in cadences]), len(cadences)))
    return out


def longest_run(rows: list, weeks: list) -> list:
    out = []
    for label in weeks:
        distances = [row.km for row in rows
                     if row.week == label and row.type in RUN_TYPES and row.km]
        out.append((label, max(distances) if distances else None))
    return out


@dataclass
class HrDrift:
    date: dt.date
    pace_s: float
    hr: float
    reference: float
    n: int

    @property
    def delta(self) -> float:
        return self.hr - self.reference

    def __str__(self) -> str:
        minutes, seconds = divmod(round(self.pace_s), 60)
        return (f"{self.date:%d/%m} à {minutes}:{seconds:02d}/km : {self.hr:.0f} bpm "
                f"contre {self.reference:.0f} (médiane de {self.n} EF voisines, "
                f"{self.delta:+.0f})")


def _flat_easy_runs(rows: list) -> list:
    """Comparable easy runs: flat, long enough, with timing and HR, not in the heat.

    An HR declared doubtful enters neither the reference nor the comparison, whatever the
    reason: without the armband it is wrong, with a context (meal, illness) it measures
    something other than fatigue.
    """
    runs = [row for row in rows
            if row.type == session_kinds.EASY and row.km and row.km >= HR_MIN_KM and row.hours
            and row.hr_avg and not getattr(row, "hr_doubt", None)
            and row.dplus / row.km <= HR_FLAT_M_PER_KM
            and (row.wbgt is None or row.wbgt < HR_HOT_WBGT_C)]
    return sorted(runs, key=lambda row: row.date)


def hr_at_pace(rows: list) -> list[HrDrift]:
    """Each easy run compared to the median of same-pace easy runs of the six weeks before."""
    runs = _flat_easy_runs(rows)
    out = []
    for run in runs:
        pace = run.hours * 3600 / run.km
        reference = [other.hr_avg for other in runs
                     if other is not run
                     and 0 < (run.date - other.date).days <= HR_BASELINE_DAYS
                     and abs(other.hours * 3600 / other.km - pace) <= HR_PACE_BAND_S]
        if len(reference) >= HR_BASELINE_MIN:
            out.append(HrDrift(run.date, pace, float(run.hr_avg),
                               float(statistics.median(reference)), len(reference)))
    return out


def signals(rows: list, weeks: list, intentional_keys: frozenset[str]) -> list:
    """What deserves a look this week. Empty = nothing to report, and that is normal."""
    found = []

    cadences = [(label, value) for label, value, n in cadence_at_easy_pace(
        rows, weeks, intentional_keys)
                if value is not None]
    if len(cadences) >= 2:
        (_, before), (label, now) = cadences[-2], cadences[-1]
        if now - before >= CADENCE_JUMP:
            found.append(Signal(
                "cadence en hausse à allure facile",
                f"{before:.0f} → {now:.0f} pas/min ({label}). Cause inconnue : vérifier "
                "d'abord si la cadence était prescrite ou volontaire, puis le terrain, "
                "l'allure, la facilité du geste et la douleur"))

    drifts = hr_at_pace(rows)
    recent = [drift for drift in drifts if drift_week(drift) in weeks]
    if len(recent) >= 2 and all(drift.delta >= HR_DRIFT_BPM for drift in recent[-2:]):
        found.append(Signal(
            "FC haute à allure comparable, deux EF de suite",
            " ; ".join(str(drift) for drift in recent[-2:]) + ". Alarme n°4 du plan "
            "(elle a précédé mars 2026). Vérifier d'abord sommeil, maladie, chaleur "
            "non mesurée et terrain : un signal n'est pas une cause"))

    return found


def drift_week(drift: HrDrift) -> str:
    year, week, _ = drift.date.isocalendar()
    return f"{year}-W{week:02d}"


def render(rows: list, weeks: list, intentional_keys: frozenset[str],
           today: dt.date | None = None) -> str:
    lines = ["", f"  Signaux faibles — {weeks[0]} → {weeks[-1]}", ""]
    lines.append("  Cadence (EF/SL, pas/min)   " + " · ".join(
        f"{label[-3:]} {value:.0f}" if value else f"{label[-3:]} —"
        for label, value, _ in cadence_at_easy_pace(rows, weeks, intentional_keys)))
    lines.append("  Plus longue (km)           " + " · ".join(
        f"{label[-3:]} {value:.1f}" if value else f"{label[-3:]} —"
        for label, value in longest_run(rows, weeks)))
    # The slope: scenario 9 reads on weeks that no longer progress.
    stall = gauge.long_run_stall(rows, today or dt.date.today())
    if stall:
        lines.append(f"  {stall}")
    drifts = [drift for drift in hr_at_pace(rows) if drift_week(drift) in weeks]
    lines.append("  FC à allure (EF plates)    " + (" · ".join(
        f"{drift.date:%d/%m} {drift.delta:+.0f}" for drift in drifts)
        or "aucune EF comparable"))
    found = signals(rows, weeks, intentional_keys)
    lines += ["", *[str(signal) for signal in found]] if found else [
        "", "  Rien à signaler sur ces semaines."]
    lines += ["", "  Les 4 contrôles Strava du § D (allure en descente, parcours écourtés,",
              "  pauses en fin de sortie, longueur des notes) restent à lire chez Strava.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=paths.vault_root(), type=Path)
    parser.add_argument("--semaines", dest="weeks", type=int, default=4)
    args = parser.parse_args()

    store = gauge.open_store()
    rows = gauge.parse_ledger(store)
    intentional = intentional_cadence_keys(store)
    weeks = _weeks(rows, args.weeks)
    if not weeks:
        print("Registre vide : rien à lire.")
        return 1
    print(render(rows, weeks, intentional))
    return 0

