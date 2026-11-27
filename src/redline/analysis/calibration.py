"""When a calibration of fitness is adopted, and when only a test is proposed.

Strong evidence adopts: an all-out effort the athlete declared (a test or a race) newer
than the evidence of the calibration in force. The critical speed and D′ are then fitted
again on the short efforts of their window (`critical_speed`), the VDOT filtered again on
the longer ones (`race_estimate`), and the new calibration is dated, kept and decided by
rule. Without a calibration yet, the first one is adopted the same way, as `initial`. A
calibration whose newest effort is older than `calibration_fresh_weeks` is provisional.

Weak evidence never adopts. A session held beyond the calibration's band (its VDOT plus
`calibration_band_sd` standard deviations), or a declared break after its newest effort,
proposes a test instead: the paces stay what the calibration says until a test speaks.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from redline.model import rulebook
from redline.model.calibration import Calibration
from redline.model.efforts import Effort
from redline.model.vocabulary import (
    CalibrationConfidence,
    CalibrationSource,
    Decider,
    EffortKind,
)

from . import critical_speed, race_estimate


@dataclass(frozen=True)
class Settings:
    fresh_weeks: int
    estimate: race_estimate.Settings | None
    critical: critical_speed.Settings | None


def settings() -> Settings | None:
    """The thresholds of the rulebook in force, or None: no calibration is adopted. One of
    the estimate or the critical speed at least must be declared."""
    book = rulebook.current().parameters
    estimate, critical = race_estimate.settings(), critical_speed.settings()
    if (book.calibration_band_sd is None or book.calibration_fresh_weeks is None
            or (estimate is None and critical is None)):
        return None
    return Settings(book.calibration_fresh_weeks, estimate, critical)


def adoption(current: Calibration | None, efforts: Iterable[Effort], today: dt.date,
             config: Settings) -> Calibration | None:
    """The calibration to adopt today, or None when no declared effort is newer than the
    evidence of the one in force."""
    efforts = list(efforts)
    form = race_estimate.form(efforts, today, config.estimate) if config.estimate else None
    fit = critical_speed.fit(efforts, today, config.critical) if config.critical else None
    read = {effort.key: effort for effort in (*(form.efforts if form else ()),
                                              *(fit.efforts if fit else ()))}
    if not read:
        return None
    newest = max(read.values(), key=lambda effort: (effort.on, effort.key))
    if current is not None:
        held = max((effort.on for effort in efforts if effort.key in current.evidence),
                   default=dt.date.min)
        if newest.key in current.evidence or newest.on <= held:
            return None
    source = (CalibrationSource.INITIAL if current is None
              else CalibrationSource.TEST if newest.kind == EffortKind.TEST
              else CalibrationSource.RACE)
    fresh = (today - newest.on).days <= config.fresh_weeks * 7
    return Calibration(
        today, CalibrationConfidence.MEASURED if fresh else CalibrationConfidence.PROVISIONAL,
        source, tuple(sorted(read)), Decider.RULE,
        cs_mps=round(fit.speed_mps, 3) if fit else None,
        d_prime_m=round(fit.d_prime_m, 1) if fit else None,
        cs_se_mps=round(fit.speed_se, 3) if fit and fit.speed_se is not None else None,
        vdot=round(form.vdot, 2) if form else None,
        vdot_sd=round(form.vdot_sd, 2) if form else None,
        hr_first_threshold_bpm=current.hr_first_threshold_bpm if current else None,
        hr_second_threshold_bpm=current.hr_second_threshold_bpm if current else None)
