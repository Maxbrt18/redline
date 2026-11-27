"""The dose of a quality session, repetition by repetition: the WER method.

Desgorces et al. (2007; equation 3 of Desgorces et al. 2020): load = the work accumulated
over the endurance limit at its intensity + ln(1 + accumulated work duration / accumulated
recovery duration). Each repetition read on the laps (`redline.model.debrief.workout_reps`)
counts the share of its limit time it uses; the rest of the run, warm-up and cool-down,
counts the same way at its own mean speed; the recoveries enter through the logarithm.
So 5 × 7 min and 7 × 5 min at the same pace use the same share of their limit and differ
by their recoveries, four against six.

The limit time at a speed: above the critical speed, D′ / (speed − CS), the two-parameter
model (`redline.analysis.critical_speed`); otherwise Daniels' curve at the athlete's VDOT
(`redline.analysis.race_estimate`), the duration whose sustained share equals the speed's
oxygen cost over the VDOT. Below the curve's floor, 80 % of the VDOT, no limit holds and
the stretch adds nothing. Below CS, D′ is not drawn on: a threshold session costs the time
spent in the heavy domain, which the share of the limit time measures and a W′ balance
would count as nothing.

Without a VDOT, or without repetitions read on the laps, a session has no such dose.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

from redline.model import efforts as effort_model
from redline.model import session_kinds

from . import critical_speed, easy_load, race_estimate
from .ledger import Row

#: Daniels' sustained share never falls below this: a slower intensity has no limit.
SHARE_FLOOR = 0.8


def limit_s(speed_mps: float, points: float, fit: critical_speed.Fit | None = None) -> float | None:
    """The time to exhaustion at a speed, in seconds; None where no limit holds."""
    if fit is not None and speed_mps > fit.speed_mps:
        return fit.d_prime_m / (speed_mps - fit.speed_mps)
    share = race_estimate.oxygen_cost(speed_mps * 60) / points
    if share <= SHARE_FLOOR:
        return None
    low, high = race_estimate.SHORTEST_MIN, race_estimate.LONGEST_MIN
    if share >= race_estimate.sustained_share(low):
        return low * 60
    while high - low > race_estimate.PRECISION_MIN:
        middle = (low + high) / 2
        if race_estimate.sustained_share(middle) > share:
            low = middle
        else:
            high = middle
    return (low + high) / 2 * 60


def _share(metres: float, seconds: float, points: float, fit: critical_speed.Fit | None) -> float:
    if metres <= 0 or seconds <= 0:
        return 0.0
    limit = limit_s(metres / seconds, points, fit)
    return 0.0 if limit is None else seconds / limit


def wer(reps: Sequence[dict], km: float, seconds: float, points: float,
        fit: critical_speed.Fit | None = None) -> float | None:
    """The WER load of a run of `km` in `seconds` with these repetitions; None without a
    recovery between them."""
    work_s = sum(rep["time_s"] for rep in reps)
    recovery_s = sum(rep["recovery"]["time_s"] for rep in reps if "recovery" in rep)
    if not reps or recovery_s <= 0:
        return None
    used = sum(_share(rep["dist_m"], rep["time_s"], points, fit) for rep in reps)
    rest_m = km * 1000 - sum(rep["dist_m"] for rep in reps) - sum(
        rep["recovery"]["dist_m"] for rep in reps if "recovery" in rep)
    used += _share(rest_m, seconds - work_s - recovery_s, points, fit)
    return used + math.log(1 + work_s / recovery_s)


def quality_doses(store: Any) -> Callable[[Row], float | None]:
    """The WER load of each quality run, from the declared efforts of the days before it:
    its VDOT by the goal estimate's filter, its critical speed when one is fitted. None
    for a run without either a VDOT or its repetitions."""
    estimate_config, speed_config = race_estimate.settings(), critical_speed.settings()
    declared = effort_model.efforts(store.effort_rows()) if estimate_config else []

    def dose(row: Row) -> float | None:
        if estimate_config is None or row.type not in session_kinds.QUALITY \
                or not row.km or not row.hours:
            return None
        before = [effort for effort in declared if effort.on < row.date]
        found = race_estimate.estimate(before, [], row.date, 10.0, estimate_config)
        reps = easy_load.stored_reps(store, row)
        if found is None or not reps:
            return None
        fit = (critical_speed.fit(before, row.date, speed_config)
               if speed_config is not None else None)
        return wer(reps, row.km, row.hours * 3600, found.vdot, fit)

    return dose


def leave_one_out(points: Sequence[tuple[float, float]],
                  fit_of: Callable[[Sequence[tuple[float, float]]], Any]) -> list[float]:
    """The absolute error of each (dose, RPE) point predicted from the others."""
    errors = []
    for index, (dose, rpe) in enumerate(points):
        model = fit_of([*points[:index], *points[index + 1:]])
        if model is not None and dose > 0:
            errors.append(abs(model.intercept + model.slope * math.log(dose) - rpe))
    return errors
