"""The climbs of a route: where each one starts and tops out, its gain and grades, and the
pieces of homogeneous grade a runner feels on it.

Pipeline, on the elevation along the route:

1. **Resampling** on a regular grid of STEP_M, between two passes of a Hampel filter in
   metres: a sample straying from the median of its ±HAMPEL_HALF_WINDOW_M by more than
   max(HAMPEL_FLOOR_M, HAMPEL_SIGMAS × 1.4826 × MAD) is replaced by that median.
2. **Two Gaussian scales**: a local one (LOCAL_SIGMA_M) for boundaries and statistics, a
   structural one (STRUCTURAL_SIGMA_M) that alone decides whether a climb exists.
3. **Hysteresis on the structural grade**: a candidate opens at ENTER_GRADE_PCT and becomes a
   climb once MIN_CLIMB_LENGTH_M long with MIN_CLIMB_GAIN_M of gain. A climb ends when it has
   lost MAX_DRAWDOWN_M for DRAWDOWN_CONFIRM_M, or when its trend above CONTINUE_GRADE_PCT
   has not progressed for MAX_NONCLIMB_M.
4. **Boundaries refined on the local signal**: the first significant uphill phase starts the
   climb, the highest summit of its phases ends it. A gentle false flat stays in the climb; a
   rebound after a real dip does not.
5. **Statistics** on the local signal, and an importance mixing gain and steep grade: the
   main climbs are the ones a runner would name.

Each climb is then cut where its grade changes: an optimal partition into sections of
constant grade (squared grade error weighted by length, plus a penalty per section), then
the adjacent sections whose grades differ by less than half GRADE_CHANGE_PCT are merged.
Fixed chunks would cut a steep ramp in half and dilute it.

The thresholds are engineering settings for running on foot, not calibrated against climbs
labelled by runners.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

STEP_M = 5.0
LOCAL_SIGMA_M = 25.0
STRUCTURAL_SIGMA_M = 90.0

ENTER_GRADE_PCT = 2.0
CONTINUE_GRADE_PCT = 0.45
MIN_CLIMB_LENGTH_M = 120.0
MIN_CLIMB_GAIN_M = 10.0
MAX_DRAWDOWN_M = 22.0
DRAWDOWN_CONFIRM_M = 60.0
MAX_NONCLIMB_M = 550.0

#: An uphill phase inside a climb, used to refine its boundaries.
PHASE_MIN_LENGTH_M = 60.0
PHASE_MIN_GAIN_M = 5.0

IMPORTANCE_GAIN_SCALE_M = 65.0
IMPORTANCE_GRADE_SCALE_PCT = 10.0
MAIN_IMPORTANCE = 0.47

#: No grade section is shorter, and two sections of that length are told apart only when
#: their grades differ by at least GRADE_CHANGE_PCT (longer ones need less).
SECTION_MIN_LENGTH_M = 150.0
GRADE_CHANGE_PCT = 2.5

GAUSSIAN_TRUNCATE_SIGMAS = 3.0
HAMPEL_HALF_WINDOW_M = 50.0
HAMPEL_SIGMAS = 4.0
HAMPEL_FLOOR_M = 8.0
#: A gap between two source samples beyond which the profile is not trusted there.
LARGE_GAP_M = 200.0
#: A refined climb keeps at least this share of the minimum length and gain.
REFINED_MINIMUM_FRACTION = 0.80
PHASE_GAP_TOLERANCE_SIGMAS = 2.0
SEARCH_EXPANSION_SIGMAS = 1.0
#: Candidate boundaries of the grade partition: keeps its quadratic cost bounded on a very
#: long climb, whose boundaries then snap to a slightly coarser grid than STEP_M.
MAX_SECTION_CANDIDATES = 1500


@dataclass(frozen=True)
class Section:
    start_m: float
    end_m: float
    #: Net grade over the section, in percent.
    grade_pct: float


@dataclass(frozen=True)
class Climb:
    #: Distance from the route start where the climb begins and tops out, in metres.
    start_m: float
    end_m: float
    gain_m: float
    #: Net grade over the climb, in percent.
    grade_pct: float
    #: A climb a runner would name: enough gain, or steep enough.
    main: bool
    #: Consecutive, without gaps, from `start_m` to `end_m`.
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class _Series:
    """The regular grid and its local signal, as the refinement reads them."""

    s: np.ndarray
    z_local: np.ndarray
    g_local: np.ndarray
    supported: np.ndarray
    gain_prefix: np.ndarray


def detect(distance_m: np.ndarray, elevation_m: np.ndarray) -> list[Climb]:
    """The climbs of a route, in order, from its elevation along the distance."""
    source = _source(np.asarray(distance_m, dtype=float), np.asarray(elevation_m, dtype=float))
    if source is None or source[0][-1] - source[0][0] < STEP_M:
        return []
    s, z, gaps = _preprocessed(*source)
    supported = _support(s, gaps)
    z_local = _smooth(z, LOCAL_SIGMA_M / STEP_M)
    z_struct = _smooth(z, STRUCTURAL_SIGMA_M / STEP_M)
    g_local = _grade(z_local, s)
    series = _Series(s, z_local, g_local, supported, _gain_prefix(z_local)[0])
    climbs = []
    for envelope in _envelopes(s, z_struct, _grade(z_struct, s)):
        start, summit = _refine(envelope, series)
        length = s[summit] - s[start]
        gain = series.gain_prefix[summit] - series.gain_prefix[start]
        if (length >= REFINED_MINIMUM_FRACTION * MIN_CLIMB_LENGTH_M
                and gain >= REFINED_MINIMUM_FRACTION * MIN_CLIMB_GAIN_M):
            climbs.append(_climb(start, summit, series))
    return sorted(climbs, key=lambda climb: climb.start_m)


# ------------------------------------------------------------ preprocessing
def _source(distance: np.ndarray, elevation: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """The samples that move along the route (more than 5 cm), so distance strictly grows."""
    if distance.size < 2:
        return None
    keep = [0]
    for index in range(1, distance.size):
        if distance[index] - distance[keep[-1]] > 0.05:
            keep.append(index)
    return (distance[keep], elevation[keep]) if len(keep) >= 2 else None


def _preprocessed(s0: np.ndarray, z0: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray, list[tuple[float, float]]]:
    first, total = float(s0[0]), float(s0[-1])
    steps = np.diff(s0)
    gaps = [(float(s0[i]), float(s0[i + 1])) for i in np.flatnonzero(steps > LARGE_GAP_M)]
    despiked = _hampel(s0, z0)
    start = math.ceil(first / STEP_M) * STEP_M
    s = np.arange(start, total, STEP_M)
    if s.size == 0:
        s = np.array([total])
    elif total - s[-1] > STEP_M * 0.25:
        s = np.append(s, total)
    else:
        s[-1] = total
    return s, _hampel(s, np.interp(s, s0, despiked)), gaps


def _support(s: np.ndarray, gaps: list[tuple[float, float]]) -> np.ndarray:
    supported = np.ones(s.size, dtype=bool)
    for low, high in gaps:
        supported &= ~((s > low) & (s < high))
    return supported


def _hampel(s: np.ndarray, z: np.ndarray) -> np.ndarray:
    out = z.copy()
    low = 0
    high = 0
    for index in range(z.size):
        while low < z.size and s[low] < s[index] - HAMPEL_HALF_WINDOW_M:
            low += 1
        high = max(high, index)
        while high + 1 < z.size and s[high + 1] <= s[index] + HAMPEL_HALF_WINDOW_M:
            high += 1
        window = z[low:high + 1]
        if window.size < 3:
            continue
        median = float(np.median(window))
        mad = float(np.median(np.abs(window - median)))
        if abs(z[index] - median) > max(HAMPEL_FLOOR_M, HAMPEL_SIGMAS * 1.4826 * mad):
            out[index] = median
    return out


def _smooth(z: np.ndarray, sigma_samples: float) -> np.ndarray:
    """Gaussian truncated at GAUSSIAN_TRUNCATE_SIGMAS and renormalized at the edges: no
    padding nor reflection, so a constant signal stays constant up to its ends."""
    radius = max(1, math.ceil(GAUSSIAN_TRUNCATE_SIGMAS * sigma_samples))
    offsets = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (offsets / sigma_samples) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(z, radius)
    weights = np.pad(np.ones_like(z), radius)
    return (np.convolve(padded, kernel, mode="valid")
            / np.convolve(weights, kernel, mode="valid"))


def _grade(z: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Centred grade in percent, one-sided at the ends."""
    if z.size < 2:
        return np.zeros(z.size)
    grade = np.empty(z.size)
    grade[0] = 100.0 * (z[1] - z[0]) / (s[1] - s[0])
    grade[-1] = 100.0 * (z[-1] - z[-2]) / (s[-1] - s[-2])
    if z.size > 2:
        grade[1:-1] = 100.0 * (z[2:] - z[:-2]) / (s[2:] - s[:-2])
    return grade


def _gain_prefix(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    steps = np.diff(z, prepend=z[0])
    return np.cumsum(np.maximum(steps, 0)), np.cumsum(np.maximum(-steps, 0))


# ------------------------------------------------------------ structural detection
@dataclass(frozen=True)
class _Envelope:
    start: int
    summit: int
    trigger_end: int


def _confirmed(s: np.ndarray, z: np.ndarray, gains: np.ndarray, start: int, index: int) -> bool:
    return (s[index] - s[start] >= MIN_CLIMB_LENGTH_M
            and gains[index] - gains[start] >= MIN_CLIMB_GAIN_M
            and z[index] - z[start] >= 0.75 * MIN_CLIMB_GAIN_M)


def _envelopes(s: np.ndarray, z: np.ndarray, grade: np.ndarray) -> list[_Envelope]:
    """Flat, candidate, climb, flat: the hysteresis on the structural signal."""
    n = s.size
    if n < 3:
        return []
    gains = _gain_prefix(z)[0]
    trend = z - CONTINUE_GRADE_PCT / 100.0 * s
    abort_m = min(500.0, max(MIN_CLIMB_LENGTH_M, 200.0))
    state = "flat"
    start = high = trend_high = 0
    trend_value = -math.inf
    exceeded: int | None = None
    envelopes = []
    for index in range(n):
        if state == "flat":
            if grade[index] >= ENTER_GRADE_PCT:
                state, start, high, trend_high = "candidate", index, index, index
                trend_value, exceeded = trend[index], None
            continue
        if z[index] >= z[high]:
            high = index
        if trend[index] >= trend_value:
            trend_value, trend_high = trend[index], index
        lag_m = s[index] - s[trend_high]
        drawdown = z[high] - z[index]
        if state == "candidate":
            if _confirmed(s, z, gains, start, index):
                state, exceeded = "climb", None
            elif lag_m >= abort_m or drawdown >= max(MIN_CLIMB_GAIN_M, 0.6 * MAX_DRAWDOWN_M):
                state, exceeded = "flat", None
            continue
        # Where the drop below the summit first passed MAX_DRAWDOWN_M, while it lasts.
        exceeded = (exceeded if exceeded is not None else index) \
            if drawdown >= MAX_DRAWDOWN_M else None
        deep_drop = exceeded is not None and s[index] - s[exceeded] >= DRAWDOWN_CONFIRM_M
        if deep_drop or lag_m >= MAX_NONCLIMB_M:
            envelopes.append(_Envelope(start, high, index))
            state, exceeded = "flat", None
    if state == "climb" or (state == "candidate" and _confirmed(s, z, gains, start, n - 1)):
        envelopes.append(_Envelope(start, high, n - 1))
    return envelopes


# ------------------------------------------------------------ refinement
def _index_at(s: np.ndarray, metres: float) -> int:
    """The first sample at or beyond `metres`, clamped to the grid."""
    return min(int(np.searchsorted(s, metres, side="left")), s.size - 1)


def _snap(index: int, supported: np.ndarray, *, earlier: bool) -> int:
    """The nearest supported sample; a tie goes to the earlier or the later one."""
    index = min(max(index, 0), supported.size - 1)
    if supported[index]:
        return index
    valid = np.flatnonzero(supported)
    if valid.size == 0:
        return index
    distance = np.abs(valid - index)
    nearest = valid[distance == distance.min()]
    return int(nearest.min() if earlier else nearest.max())


def _phases(start: int, summit: int, series: _Series) -> list[tuple[int, int]]:
    """The uphill phases between two samples, used only to refine a climb's boundaries."""
    if summit <= start:
        return []
    s, gains = series.s, series.gain_prefix
    enter = max(1.0, CONTINUE_GRADE_PCT + 0.5)
    keep_on = max(0.0, 0.5 * CONTINUE_GRADE_PCT)
    tolerance_m = PHASE_GAP_TOLERANCE_SIGMAS * LOCAL_SIGMA_M
    phases: list[tuple[int, int]] = []
    active = False
    phase_start = start
    below: int | None = None
    last_supported = start

    def close(end_raw: int) -> None:
        end = min(end_raw, summit)
        if (end > phase_start and s[end] - s[phase_start] >= PHASE_MIN_LENGTH_M
                and gains[end] - gains[phase_start] >= PHASE_MIN_GAIN_M):
            phases.append((phase_start, end))

    for index in range(start, summit + 1):
        if not series.supported[index]:
            if active:
                close(max(phase_start + 1, last_supported))
                active, below = False, None
            continue
        last_supported = index
        grade = series.g_local[index]
        if not active:
            if grade >= enter:
                active, phase_start, below = True, index, None
            continue
        if grade < keep_on:
            if below is None:
                below = index
            elif s[index] - s[below] >= tolerance_m:
                close(max(phase_start + 1, below - 1))
                active, below = False, None
        else:
            below = None
    if active:
        close(last_supported)
    return phases


def _phase_summit(low: int, high: int, series: _Series) -> int:
    """The top of a phase: the highest point before the signal drops by a few metres."""
    last = series.s.size - 1
    low = min(max(low, 0), last)
    high = min(max(high, low), last)
    tail_m = max(2.0, 0.25 * PHASE_MIN_GAIN_M)
    peak = _snap(low, series.supported, earlier=False)
    peak_z = series.z_local[peak]
    for index in range(peak + 1, high + 1):
        if not series.supported[index]:
            break
        if series.z_local[index] >= peak_z:
            peak, peak_z = index, series.z_local[index]
        elif peak_z - series.z_local[index] >= tail_m:
            break
    return peak


def _refine(envelope: _Envelope, series: _Series) -> tuple[int, int]:
    """Start and summit on the local signal; the coarse envelope, snapped to a supported
    sample, when no significant phase is found."""
    s, z, supported = series.s, series.z_local, series.supported
    last = s.size - 1
    reach = SEARCH_EXPANSION_SIGMAS * STRUCTURAL_SIGMA_M
    search_start = max(0, min(_index_at(s, s[envelope.start] - reach), last))
    search_end = min(last, max(search_start + 1,
                               min(_index_at(s, s[envelope.trigger_end] + reach), last)))
    phases = _phases(search_start, search_end, series)
    if phases:
        start = phases[0][0]
        nexts = [following - 1 for following, _ in phases[1:]] + [search_end]
        summits = [_phase_summit(end, max(end, until), series)
                   for (_, end), until in zip(phases, nexts, strict=True)]
        summit = max(summits, key=lambda index: (z[index], index))
    else:
        start = _snap(envelope.start, supported, earlier=True)
        low = max(0, _index_at(s, s[envelope.summit] - STRUCTURAL_SIGMA_M))
        high = min(last, _index_at(s, s[envelope.summit] + STRUCTURAL_SIGMA_M))
        candidates = [index for index in range(low, high + 1) if supported[index]]
        summit = (max(candidates, key=lambda index: z[index]) if candidates
                  else _snap(envelope.summit, supported, earlier=False))
    start = _snap(start, supported, earlier=True)
    summit = _snap(summit, supported, earlier=False)
    if summit <= start:
        start = _snap(envelope.start, supported, earlier=True)
        summit = _snap(max(envelope.summit, start + 1), supported, earlier=False)
    return start, summit


# ------------------------------------------------------------ statistics and sections
def _saturating(value: float, scale: float) -> float:
    return 1.0 - math.exp(-max(0.0, value) / max(1e-9, scale))


def _climb(start: int, summit: int, series: _Series) -> Climb:
    s, z = series.s, series.z_local
    gains, losses = _gain_prefix(z)
    length = s[summit] - s[start]
    gain = float(gains[summit] - gains[start])
    loss = float(losses[summit] - losses[start])
    grades = series.g_local[start:summit + 1]
    grades = grades[np.isfinite(grades)]
    p90 = float(np.percentile(grades, 90)) if grades.size else 0.0
    importance = min(1.0, max(0.0, 0.85 * _saturating(gain, IMPORTANCE_GAIN_SCALE_M)
                              + 0.15 * _saturating(p90, IMPORTANCE_GRADE_SCALE_PCT)))
    cuts = _merged(_partition(s, z, start, summit), s, z, 0.5 * GRADE_CHANGE_PCT)
    sections = tuple(Section(float(s[i]), float(s[j]), _net_grade(s, z, i, j))
                     for i, j in itertools.pairwise(cuts))
    return Climb(start_m=float(s[start]), end_m=float(s[summit]), gain_m=gain,
                 grade_pct=100.0 * (gain - loss) / length if length > 0 else 0.0,
                 main=importance >= MAIN_IMPORTANCE, sections=sections)


def _net_grade(s: np.ndarray, z: np.ndarray, i: int, j: int) -> float:
    return float(100.0 * (z[j] - z[i]) / (s[j] - s[i])) if s[j] > s[i] else 0.0


def _partition(s: np.ndarray, z: np.ndarray, start: int, end: int) -> list[int]:
    """Boundaries, ends included, of the constant-grade fit minimizing the length-weighted
    squared grade error plus a penalty per section.

    A section's cost is Σ w·(g − ḡ)² over its steps (length w, grade g, net grade ḡ), read
    in O(1) from a prefix sum of w·g², since Σ w·g telescopes to its elevation change.
    Splitting a section of length 2L whose halves differ by Δ lowers the cost by L·Δ²/2:
    with the penalty below, two minimum-length halves need Δ ≥ GRADE_CHANGE_PCT.
    """
    length = s[end] - s[start]
    # A short climb (a wall, then a false flat) is still cut, down to a quarter of its
    # length, never below what the local smoothing (±2σ) resolves.
    minimum = min(SECTION_MIN_LENGTH_M, max(length / 4, 4 * LOCAL_SIGMA_M))
    if length < 2 * minimum:
        return [start, end]
    step = np.diff(s[start:end + 1])
    rises = np.diff(z[start:end + 1])
    step_grade = np.where(step > 0, 100.0 * rises / np.where(step > 0, step, 1.0), 0.0)
    squared = np.concatenate([[0.0], np.cumsum(step * step_grade ** 2)])
    penalty = 0.5 * minimum * GRADE_CHANGE_PCT ** 2
    stride = max(1, -(-(end - start) // MAX_SECTION_CANDIDATES))
    candidates = np.append(np.arange(start, end, stride), end)
    best = np.full(candidates.size, math.inf)
    previous = np.zeros(candidates.size, dtype=int)
    best[0] = 0.0
    for t in range(1, candidates.size):
        j = candidates[t]
        # Ascending candidates: from the first that leaves a section shorter than the
        # minimum, every later one would too.
        reachable = int(np.searchsorted(s[j] - s[candidates[:t]] < minimum, True))
        if reachable == 0:
            continue
        i = candidates[:reachable]
        rise = 100.0 * (z[j] - z[i])
        cost = squared[j - start] - squared[i - start] - rise * rise / (s[j] - s[i])
        values = best[:reachable] + cost + penalty
        u = int(np.argmin(values))
        if values[u] < best[t]:
            best[t], previous[t] = values[u], u
    boundaries = [int(end)]
    t = candidates.size - 1
    while t > 0:
        t = int(previous[t])
        boundaries.append(int(candidates[t]))
    return boundaries[::-1]


def _merged(boundaries: list[int], s: np.ndarray, z: np.ndarray, minimum_pct: float) -> list[int]:
    """Removes, one at a time, the boundary between the two adjacent sections of closest
    net grades while they differ by less than `minimum_pct`."""
    boundaries = list(boundaries)
    while len(boundaries) > 2:
        merge_at = None
        smallest = minimum_pct
        for k in range(1, len(boundaries) - 1):
            change = abs(_net_grade(s, z, boundaries[k], boundaries[k + 1])
                         - _net_grade(s, z, boundaries[k - 1], boundaries[k]))
            if change < smallest:
                smallest, merge_at = change, k
        if merge_at is None:
            break
        del boundaries[merge_at]
    return boundaries
