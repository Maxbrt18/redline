"""What the coach reads of the athlete's fitness, under a cycle's trame: the goal's time
estimated from the declared all-out efforts, with its controls, and the calibration in
force with the domains it renders, or the test that would give one.

A road goal only has an estimate: a trail's time depends on its course.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from redline.coach.fr import fitness as text
from redline.model import calibration as calibration_model
from redline.model import efforts as effort_model
from redline.model import instance, intensity, paths
from redline.model import race as race_model
from redline.model.calibration import Calibration
from redline.model.cycle_templates import Range
from redline.model.vocabulary import IntensityRef, RaceTerrain

from . import calibration, critical_speed, ledger, race_estimate, signals
from .calibration import RetestProposal


@dataclass(frozen=True)
class GoalReading:
    """A road goal's estimated time, the bound the recent sessions prove and the easy
    runs' efficiency since the last effort."""

    config: race_estimate.Settings
    estimate: race_estimate.Estimate | None
    bound: race_estimate.Bound | None
    efficiency: signals.Efficiency | None


def goal_reading(store: Any, rows: list[ledger.Row], goal: race_model.Goal,
                 today: dt.date) -> GoalReading | None:
    config = race_estimate.settings()
    if goal.terrain != RaceTerrain.ROAD or config is None:
        return None
    found = race_estimate.estimate(effort_model.efforts(store.effort_rows()),
                                   effort_model.breaks(store.break_rows()), today,
                                   goal.distance_km, config)
    since = (found.sources[-1].effort.on if found is not None
             else today - dt.timedelta(weeks=config.window_weeks))
    return GoalReading(config, found,
                       race_estimate.bound(race_estimate.pieces(store, rows, today, config)),
                       signals.efficiency(rows, since, today))


@dataclass(frozen=True)
class CalibrationReading:
    """The calibration in force, None before the first, the domains' shares of CS, and the
    test the weak evidence proposes."""

    calibration: Calibration | None
    critical: critical_speed.Settings | None
    proposal: RetestProposal | None = None


def calibration_reading(store: Any, rows: list[ledger.Row],
                        today: dt.date) -> CalibrationReading | None:
    """None when the rulebook declares no calibration."""
    config = calibration.settings()
    if config is None:
        return None
    current = calibration_model.in_force(store.calibration_rows())
    bound = (race_estimate.bound(race_estimate.pieces(store, rows, today, config.estimate))
             if config.estimate is not None else None)
    proposal = calibration.retest_proposal(current, effort_model.efforts(store.effort_rows()),
                                         effort_model.breaks(store.break_rows()), bound, config)
    return CalibrationReading(current, config.critical, proposal)


def _clock(seconds: float) -> str:
    hours, rest = divmod(round(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"


def _goal_line(goal: race_model.Goal) -> str:
    if goal.goal_time_s is None or goal.goal_source is None:
        return ""
    return text.GOAL.format(time=_clock(goal.goal_time_s),
                            source=text.GOAL_SOURCES[goal.goal_source])


def _source_line(source: race_estimate.Source) -> str:
    effort = source.effort
    return text.ESTIMATE_SOURCE.format(
        kind=text.EFFORT_KINDS[effort.kind], km=text.number(effort.km),
        clock=_clock(effort.seconds), on=effort.on, months=text.number(source.months),
        vdot=text.number(source.vdot), equivalent=_clock(source.equivalent_s),
        share=f"{source.share * 100:.0f}")


def _pace(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _calibration_lines(reading: CalibrationReading | None) -> list[str]:
    """The calibration in force, its critical speed and the domains around it, or the
    test that would give one."""
    if reading is None:
        return []
    found, config = reading.calibration, reading.critical
    if found is None:
        return [text.NO_CALIBRATION]
    lines = [text.CALIBRATION.format(
        on=found.adopted_on, source=text.CALIBRATION_SOURCES[found.source],
        decided=text.DECIDERS[found.decided_by],
        confidence=text.CONFIDENCES[found.confidence], n=len(found.evidence))]
    if found.vdot is not None and found.vdot_sd is not None:
        lines.append(text.CALIBRATION_VDOT.format(vdot=text.number(found.vdot),
                                                  sd=text.number(found.vdot_sd)))
    lines += _proposal_lines(reading.proposal)
    cs = found.cs_mps
    if cs is None or found.d_prime_m is None or config is None:
        return [*lines, text.NO_CRITICAL_SPEED]
    athlete = instance.load(paths.vault_root())
    refs = calibration.references(found, config, hr_rest=athlete.hr_rest,
                                  hr_max=athlete.hr_max)
    easy_hr = intensity.render(IntensityRef.HR_ZONE, Range(1, 2), refs).hr_bpm

    def pace(ref: IntensityRef, low: float, high: float) -> Range:
        rendered = intensity.render(ref, Range(low, high), refs).pace_s
        assert rendered is not None, "a calibration with CS renders its speed references"
        return rendered

    critical = pace(IntensityRef.CRITICAL_SPEED, 100, 100).low
    threshold = pace(IntensityRef.CRITICAL_SPEED, config.threshold_low_pct,
                     config.threshold_high_pct)
    error = (text.CRITICAL_SPEED_TWO if found.cs_se_mps is None else
             text.CRITICAL_SPEED_ERROR.format(
                 error=f"{1000 / (cs - found.cs_se_mps) - critical:.0f}"))
    return [*lines,
            text.CRITICAL_SPEED.format(pace=_pace(critical), speed=text.number(cs * 3.6),
                                       d_prime=f"{found.d_prime_m:.0f}", error=error),
            text.CRITICAL_SPEED_DOMAINS.format(
                easy=_pace(pace(IntensityRef.FIRST_THRESHOLD, 100, 100).low),
                hr=f"{easy_hr.high:.0f}" if easy_hr is not None else "?",
                first=text.number(config.first_threshold_pct),
                low=_pace(threshold.low), high=_pace(threshold.high),
                low_pct=text.number(config.threshold_low_pct),
                high_pct=text.number(config.threshold_high_pct), pace=_pace(critical))]


def _proposal_lines(proposal: RetestProposal | None) -> list[str]:
    """The test the weak evidence proposes; the calibration does not move before it."""
    if proposal is None:
        return []
    if proposal.vdot is not None and proposal.band_top is not None:
        return [text.TEST_SESSIONS_ABOVE.format(on=proposal.on, vdot=text.number(proposal.vdot),
                                                top=text.number(proposal.band_top))]
    return [text.TEST_AFTER_BREAK.format(on=proposal.on)]


def _estimate_lines(reading: GoalReading | None) -> list[str]:
    """What the declared efforts say of a road goal's time, with its controls."""
    if reading is None:
        return []
    found, config = reading.estimate, reading.config
    if found is None:
        lines = [text.NO_ESTIMATE.format(km=text.number(config.min_source_km),
                                         weeks=config.window_weeks)]
    else:
        lines = [text.ESTIMATE.format(time=_clock(found.time_s), low=_clock(found.low_s),
                                      high=_clock(found.high_s), band=found.band_pct,
                                      vdot=text.number(found.vdot),
                                      sd=text.number(found.vdot_sd))]
        if found.capped_by is not None:
            lines.append(text.ESTIMATE_CAPPED.format(start=found.capped_by.starts_on,
                                                     end=found.capped_by.ends_on,
                                                     time=_clock(found.time_s)))
        if len(found.sources) == 1:
            lines.append(text.ESTIMATE_SINGLE.format(months=text.number(found.sources[0].months)))
        lines += [_source_line(source) for source in found.sources]
    held = reading.bound
    if held is not None:
        above = found is not None and held.vdot > found.vdot
        lines.append(text.BOUND.format(weeks=config.bound_weeks, vdot=text.number(held.vdot),
                                       km=text.number(held.km), clock=_clock(held.seconds),
                                       on=held.on) + (text.BOUND_ABOVE if above else ""))
    trend = reading.efficiency
    if trend is not None:
        lines.append(text.EFFICIENCY.format(
            change=f"{trend.change_pct:+.1f}".replace(".", ","), since=trend.since,
            before=f"{trend.before:.2f}".replace(".", ","),
            now=f"{trend.now:.2f}".replace(".", ","), runs_before=trend.runs_before,
            runs_now=trend.runs_now))
    return lines



def lines(store: Any, today: dt.date, goal: race_model.Goal | None) -> list[str]:
    """The goal's estimate, for a road goal, then the calibration in force."""
    rows = ledger.parse_ledger(store)
    stated = _goal_line(goal) if goal is not None else ""
    reading = goal_reading(store, rows, goal, today) if goal is not None else None
    return [*([stated] if stated else []), *_estimate_lines(reading),
            *_calibration_lines(calibration_reading(store, rows, today))]
