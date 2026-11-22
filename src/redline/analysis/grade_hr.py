"""The athlete's easy HR by grade: what the HR of a climb is held against, never the
flat cap.

Dated like the reference easy pace (`redline.analysis.easy_load`): the solo easy runs of
the `easy_load.REFERENCE_WEEKS` weeks before the day, each cut into its stretches. A
stretch walked, or without HR, is left out. The stretches fall in bins of `BIN_PCT`
points of grade centred on even grades (the flat is -1 to 1 %); a bin's HR is the median
of its stretches, kept from `MIN_STRETCHES` stretches over `MIN_RUNS` runs. Nothing is
interpolated between kept bins: a grade whose bin is not kept is not judged. Descents
are left out: their HR says little of the effort.

The HR lags the effort by tens of seconds, so a stretch at the foot of a climb reads
part of the flat before it. The curve and the climbs it judges read stretches the same
way: the lag is on both sides.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from redline.analysis import easy_load

BIN_PCT = 2
#: Below this grade, a stretch descends: no bin.
LOWEST_GRADE_PCT = -1.0
#: Above this grade, a stretch's altitude is more likely a glitch than a slope.
HIGHEST_GRADE_PCT = 30.0
MIN_STRETCHES = 6
MIN_RUNS = 3


@dataclass(frozen=True)
class Bin:
    """The easy HR at one grade: its centre, in %, the median HR and what it rests on."""

    grade_pct: int
    hr: float
    stretches: int
    runs: int


@dataclass(frozen=True)
class Curve:
    """The athlete's easy HR by grade on `on`, from `runs` easy runs."""

    on: dt.date
    runs: int
    bins: tuple[Bin, ...]

    def hr_at(self, grade_pct: float) -> float | None:
        """The usual easy HR at this grade, or None when its bin is not kept."""
        centre = bin_of(grade_pct)
        return next((item.hr for item in self.bins if item.grade_pct == centre), None)


def bin_of(grade_pct: float) -> int | None:
    """The centre of the bin a grade falls in, or None outside the curve."""
    if not LOWEST_GRADE_PCT <= grade_pct <= HIGHEST_GRADE_PCT:
        return None
    return math.floor(grade_pct / BIN_PCT + 0.5) * BIN_PCT


def climbing(grade_pct: float) -> bool:
    """A grade past the flat bin, on the curve."""
    return (bin_of(grade_pct) or 0) > 0


def curve(runs: Iterable[tuple[dt.date, Sequence[easy_load.Stretch]]],
          day: dt.date) -> Curve | None:
    """The curve on `day` from the runs of the weeks before it; None without a kept bin."""
    since = day - dt.timedelta(weeks=easy_load.REFERENCE_WEEKS)
    found: dict[int, list[tuple[int, float]]] = {}
    used = set()
    for index, (on, stretches) in enumerate(runs):
        if not since <= on < day:
            continue
        for stretch in stretches:
            centre = bin_of(stretch.grade_pct)
            if centre is None or stretch.hr is None or stretch.walked:
                continue
            found.setdefault(centre, []).append((index, stretch.hr))
            used.add(index)
    bins = tuple(
        Bin(centre, float(statistics.median(hr for _, hr in members)), len(members),
            len({index for index, _ in members}))
        for centre, members in sorted(found.items())
        if len(members) >= MIN_STRETCHES and len({index for index, _ in members}) >= MIN_RUNS)
    return Curve(day, len(used), bins) if bins else None


def curve_on(store: Any, rows: Iterable[Any], day: dt.date) -> Curve | None:
    """The curve on `day` from the ledger and the kept streams: solo runs only, a group
    sets the pace and so the HR."""
    return curve(((on, shape.stretches) for on, shape, group
                  in easy_load.easy_runs(store, rows, day) if group is False), day)
