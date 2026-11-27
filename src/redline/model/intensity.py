"""Intensities rendered from their reference: a pace, an effort or a heart rate.

A key session carries a reference and a band (`cycle_templates`), never a pace. The pace
is rendered at reading time from the calibration in force, so it follows the athlete's
fitness and is never frozen at the level of a plan's first week. A speed reference's band
is in % of its speed; the pace is its inverse, the slowest first. No pace is deduced from
another by subtraction.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import hr_zones
from .cycle_templates import Range
from .vocabulary import IntensityRef

#: The heart-rate zones a band names, Z1 to Z5.
ZONES = 5


@dataclass(frozen=True)
class References:
    """What intensities are rendered against: the calibration's speeds, the goal's speed,
    the heart rates of the instance. None: not known, the reference renders no value."""

    critical_speed_mps: float | None = None
    first_threshold_mps: float | None = None
    goal_speed_mps: float | None = None
    hr_rest: int | None = None
    hr_max: int | None = None


@dataclass(frozen=True)
class Rendered:
    ref: IntensityRef
    #: Seconds per kilometre, the slowest first.
    pace_s: Range | None = None
    #: The 1-10 effort scale.
    rpe: Range | None = None
    #: All three None: a speed reference without its speed waits for a calibration.
    hr_bpm: Range | None = None


def _speed(ref: IntensityRef, refs: References) -> float | None:
    return {IntensityRef.CRITICAL_SPEED: refs.critical_speed_mps,
            IntensityRef.FIRST_THRESHOLD: refs.first_threshold_mps,
            IntensityRef.GOAL_PACE: refs.goal_speed_mps}.get(ref)


def _heart_rates(band: Range, rest: int, peak: int) -> Range:
    """From the start of the band's lowest zone to the start of the zone above its highest,
    the maximum for Z5."""
    starts = (rest, *hr_zones.bounds(rest, peak), peak)
    low, high = int(band.low), int(band.high)
    return Range(starts[low - 1], starts[min(high, ZONES)])


def render(ref: IntensityRef, band: Range, refs: References) -> Rendered:
    """The band of `ref` as a pace, an effort or heart rates."""
    if ref == IntensityRef.RPE:
        return Rendered(ref, rpe=band)
    if ref == IntensityRef.HR_ZONE:
        if refs.hr_rest is None or refs.hr_max is None:
            return Rendered(ref)
        return Rendered(ref, hr_bpm=_heart_rates(band, refs.hr_rest, refs.hr_max))
    speed = _speed(ref, refs)
    if speed is None:
        return Rendered(ref)
    return Rendered(ref, pace_s=Range(1000 / (speed * band.low / 100),
                                      1000 / (speed * band.high / 100)))
