"""The RPE a session should cost: computed from its dose, calibrated on the athlete.

The athlete's RPE is the cost of the whole session: a longer run costs more, and so does
a faster one. No fixed band holds for every dose, so none is prescribed: the expected RPE
is an output of the dose, read against what the athlete declared after similar sessions.

The dose of a session, by family:
- a run: effort-kilometres (`km + D+ / DPLUS_M_PER_KM`, the trail equivalence) times its
  intensity to the power `rpe_intensity_exponent` (the rTSS convention: duration times
  intensity squared). The intensity is the run's flat-equivalent speed over the dated
  reference easy speed (`redline.analysis.easy_load`), so a hilly run is not counted
  easy for being slow; a flat run without streams reads its raw speed. Unknown, an easy
  or long run counts at intensity 1, the pace it is prescribed at; any other run has no
  dose;
- a hike: its effort-kilometres;
- any other session (bike, strength): its minutes.

The model, per kind (per routine for strength): RPE = a + b × ln(dose), fit by least
squares on the sessions of the `rpe_model_weeks` weeks before, with a declared RPE and a
dose. Each kilometre costs less than the previous one on a scale bounded at 10. Below
`rpe_model_min_sessions` there is no model; doses too close to tell a slope, or a slope
that would make more dose cost less, give the mean. The range is the expectation
± `rpe_range_width` (± `rpe_range_width_unsettled` below `rpe_model_settled_sessions`),
its bounds rounded: three integers at ± 1, the narrowest that is not noise for an
integer RPE. Every figure comes with its count and residual, recomputed at each
declared RPE: nothing is stored.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from redline.model import rulebook, session_kinds, terrain
from redline.model.vocabulary import RpeSide

from . import easy_load
from .ledger import Row

#: Metres of gain worth one kilometre of flat: the equivalence trail races use.
DPLUS_M_PER_KM = 100
#: Below this spread of ln(dose), in standard deviation, the doses tell no slope.
MIN_LOG_SPREAD = 0.05
RPE_MAX = 10


@dataclass(frozen=True)
class Settings:
    """The method's thresholds, read from the rulebook in force
    (`rulebook.RPE_MODEL_PARAMETERS`)."""

    weeks: int
    min_sessions: int
    settled_sessions: int
    width: float
    width_unsettled: float
    exponent: float


def settings() -> Settings | None:
    """The thresholds declared by the rulebook, or None: no expected RPE is computed."""
    book = rulebook.current().parameters
    if (book.rpe_model_weeks is None or book.rpe_model_min_sessions is None
            or book.rpe_model_settled_sessions is None or book.rpe_range_width is None
            or book.rpe_range_width_unsettled is None or book.rpe_intensity_exponent is None):
        return None
    return Settings(book.rpe_model_weeks, book.rpe_model_min_sessions,
                    book.rpe_model_settled_sessions, book.rpe_range_width,
                    book.rpe_range_width_unsettled, book.rpe_intensity_exponent)


@dataclass(frozen=True)
class Dose:
    """What a session asked of the athlete, in the unit of its family."""

    #: Effort-kilometres times intensity to the exponent for a run, effort-kilometres for
    #: a hike, minutes otherwise.
    value: float
    effort_km: float | None = None
    #: The flat-equivalent speed over the reference easy speed; None off a run.
    intensity: float | None = None


def effort_km(km: float, dplus: float) -> float:
    return km + dplus / DPLUS_M_PER_KM


def run_dose(km: float, dplus: float, intensity: float, exponent: float) -> Dose:
    effort = effort_km(km, dplus)
    return Dose(effort * intensity ** exponent, effort, intensity)


def dose_of(row: Row, profile: easy_load.Profile | None, reference_mps: float | None,
            exponent: float) -> Dose | None:
    """The dose of a recorded session; None when its family's measure is missing."""
    if row.type not in session_kinds.BY_SLUG:
        return None
    family = session_kinds.family(row.type)
    if family == "run":
        if not row.km:
            return None
        intensity = _intensity(row, profile, reference_mps)
        if intensity is None:
            if row.type not in session_kinds.EASY_RUNS:
                return None
            intensity = 1.0
        return run_dose(row.km, row.dplus, intensity, exponent)
    if family == "hike":
        return Dose(effort_km(row.km, row.dplus), effort_km(row.km, row.dplus)) if row.km else None
    if family in {"bike", "strength"} and row.hours:
        return Dose(row.hours * 60)
    return None


def _intensity(row: Row, profile: easy_load.Profile | None,
               reference_mps: float | None) -> float | None:
    if not reference_mps:
        return None
    if profile is not None and profile.flat_mps:
        return profile.flat_mps / reference_mps
    if not row.km or not row.hours:
        return None
    if terrain.of(row.km * 1000, row.dplus, row.dminus) is not terrain.Terrain.FLAT:
        return None
    return row.km * 1000 / (row.hours * 3600) / reference_mps


def prescribed_dose(session: dict, reference_mps: float | None,
                    exponent: float) -> Dose | None:
    """The dose of a planned easy or long run, as prescribed: its distance (else its
    duration at the reference easy pace), its relief target (else its cap), at the
    reference easy pace. None for any other session: its dose is not typed in distance."""
    from redline.model import prescription

    if session.get("workout") not in session_kinds.EASY_RUNS or prescription.has_efforts(session):
        return None
    km = session.get("distance_target_km") or session.get("distance_cap_km")
    if km is None and session.get("duration_cap_min") and reference_mps:
        km = session["duration_cap_min"] * 60 * reference_mps / 1000
    if not km:
        return None
    dplus = session.get("dplus_target_m")
    if dplus is None:
        dplus = session.get("dplus_cap_m") or 0
    return run_dose(float(km), float(dplus), 1.0, exponent)


@dataclass(frozen=True)
class Model:
    """RPE = intercept + slope × ln(dose), fit on `sessions` declared RPE."""

    intercept: float
    slope: float
    sessions: int
    #: Standard deviation of the declared RPE around the fit.
    residual: float

    def expected(self, dose: Dose) -> float:
        value = self.intercept + self.slope * math.log(dose.value) if dose.value > 0 else 0.0
        return min(max(value, 0.0), float(RPE_MAX))


def fit(points: Sequence[tuple[float, float]], config: Settings) -> Model | None:
    """The model of (dose, RPE) points; None below the minimum count."""
    usable = [(math.log(dose), rpe) for dose, rpe in points if dose > 0]
    if len(usable) < config.min_sessions:
        return None
    logs, rpes = [x for x, _ in usable], [y for _, y in usable]
    mean_x, mean_y = statistics.fmean(logs), statistics.fmean(rpes)
    spread = statistics.pstdev(logs)
    slope = 0.0
    if spread >= MIN_LOG_SPREAD:
        slope = sum((x - mean_x) * (y - mean_y) for x, y in usable) / sum(
            (x - mean_x) ** 2 for x in logs)
    if slope <= 0:
        slope = 0.0
    intercept = mean_y - slope * mean_x
    residuals = [y - intercept - slope * x for x, y in usable]
    deviation = math.sqrt(sum(r * r for r in residuals) / max(len(usable) - 2, 1))
    return Model(intercept, slope, len(usable), deviation)


def _round(value: float) -> int:
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class Expectation:
    """The RPE a dose should cost, its range and the history behind it."""

    expected: float
    low: int
    high: int
    sessions: int
    residual: float

    def side(self, rpe: float | None) -> RpeSide:
        if rpe is None:
            return RpeSide.UNKNOWN
        if rpe > self.high:
            return RpeSide.ABOVE
        if rpe < self.low:
            return RpeSide.BELOW
        return RpeSide.WITHIN

    def as_json(self) -> dict:
        return {"expected": round(self.expected, 1), "low": self.low, "high": self.high,
                "sessions": self.sessions, "residual": round(self.residual, 2)}


def expect(model: Model, dose: Dose, config: Settings) -> Expectation:
    width = config.width if model.sessions >= config.settled_sessions else config.width_unsettled
    expected = model.expected(dose)
    low = max(_round(expected - width), 0)
    high = min(_round(expected + width), RPE_MAX)
    return Expectation(expected, low, high, model.sessions, model.residual)


def key_of(row: Row) -> tuple[str, str | None]:
    """What a session is calibrated with: its kind, and its routine for strength."""
    return row.type, row.routine if row.type == session_kinds.STRENGTH else None


ProfileOf = Callable[[Row], "easy_load.Profile | None"]
ReferenceOf = Callable[[dt.date], "float | None"]


class History:
    """The ledger's sessions with their doses, ready to calibrate.

    `profile_of` gives the kept streams of a row, `reference_of` the reference easy speed
    on a day (m/s). Each dose is computed once.
    """

    def __init__(self, rows: Iterable[Row], profile_of: ProfileOf, reference_of: ReferenceOf,
                 config: Settings):
        self.config = config
        self.rows = sorted(rows, key=lambda row: row.date)
        self._profile_of, self._reference_of = profile_of, reference_of
        self._doses: dict[tuple, Dose | None] = {}

    def reference(self, day: dt.date) -> float | None:
        return self._reference_of(day)

    def dose(self, row: Row) -> Dose | None:
        key = (row.idempotency_key, row.date, row.type, row.km, row.hours, row.dplus)
        if key not in self._doses:
            profile = self._profile_of(row) if row.type in session_kinds.RUN else None
            self._doses[key] = dose_of(row, profile, self.reference(row.date),
                                       self.config.exponent)
        return self._doses[key]

    def model(self, key: tuple[str, str | None], before: dt.date) -> Model | None:
        """The model of a kind from the sessions of the window before `before`."""
        since = before - dt.timedelta(weeks=self.config.weeks)
        points = []
        for row in self.rows:
            if not since <= row.date < before or row.rpe is None or key_of(row) != key:
                continue
            dose = self.dose(row)
            if dose is not None:
                points.append((dose.value, row.rpe))
        return fit(points, self.config)

    def expectation(self, row: Row) -> Expectation | None:
        """What a recorded session should have cost, from the sessions before it."""
        dose = self.dose(row)
        found = self.model(key_of(row), row.date) if dose is not None else None
        return None if found is None or dose is None else expect(found, dose, self.config)

    def prescribed(self, session: dict, day: dt.date) -> Expectation | None:
        """What a planned easy or long run should cost as prescribed."""
        dose = prescribed_dose(session, self.reference(day), self.config.exponent)
        if dose is None:
            return None
        found = self.model((session["workout"], None), day)
        return None if found is None else expect(found, dose, self.config)

    def planned(self, session: dict, day: dt.date) -> float | None:
        """What a planned session should cost: its prescribed dose when it is typed, else
        the median RPE the athlete declared on its kind; None below the minimum count."""
        found = self.prescribed(session, day)
        if found is not None:
            return found.expected
        workout = session["workout"]
        key = (workout, session.get("routine") if workout == session_kinds.STRENGTH else None)
        since = day - dt.timedelta(weeks=self.config.weeks)
        rpes = [row.rpe for row in self.rows if since <= row.date < day
                and row.rpe is not None and key_of(row) == key]
        return statistics.median(rpes) if len(rpes) >= self.config.min_sessions else None

    def previous(self, row: Row) -> Row | None:
        """The latest session of the same kind before `row` with a declared RPE."""
        key = key_of(row)
        return next((other for other in reversed(self.rows)
                     if other.date < row.date and other.rpe is not None
                     and key_of(other) == key), None)


def history(store: Any, rows: Iterable[Row], config: Settings) -> History:
    """The history of the ledger with the kept streams of its runs, from the store."""
    rows = list(rows)
    profiles: dict[str, easy_load.Profile | None] = {}

    def profile_of(row: Row) -> easy_load.Profile | None:
        activity = easy_load.strava_id(row.idempotency_key)
        if activity is None:
            return None
        if row.idempotency_key not in profiles:
            try:
                profiles[row.idempotency_key] = easy_load.stored_profile(store, activity)
            except Exception:  # unreadable streams: the run reads its raw speed or none
                profiles[row.idempotency_key] = None
        return profiles[row.idempotency_key]

    samples: list[easy_load.Sample] = []
    for row in rows:
        activity = easy_load.strava_id(row.idempotency_key)
        if row.type != session_kinds.EASY or activity is None:
            continue
        shape = profile_of(row)
        if shape is not None:
            samples.extend(easy_load.samples([(row.date, shape,
                                               easy_load.group_of(store, activity))]))
    return History(rows, profile_of, lambda day: easy_load.reference_mps(samples, day), config)
