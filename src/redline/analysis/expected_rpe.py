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

What the dose does not see: heat and night. Each run reads them
(`Conditions`), and their effect on the RPE is calibrated on the athlete, never taken
from a table (`calibrate`): the RPE residuals of the runs of the window, against the
dose model, with and without the condition (per WBGT degree above `HEAT_FROM_WBGT_C` for
heat). An effect shifts a run's expectation only once `rpe_model_settled_sessions` runs
carry the condition and the effect stands more than `EFFECT_SIGNIFICANCE` standard
errors from zero; until then it is reported and changes nothing. Published work gives a
heat optimum, not an RPE cost per degree, and a darker run feeling harder without a
usable size: the athlete's own history decides. A group is no condition: the pace it
sets is already in the dose, and at a given pace it leaves the RPE about unchanged.

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
from redline.model.vocabulary import Condition, RpeSide

from . import easy_load
from .ledger import Row, row_key

#: Metres of gain worth one kilometre of flat: the equivalence trail races use.
DPLUS_M_PER_KM = 100
#: Below this spread of ln(dose), in standard deviation, the doses tell no slope.
MIN_LOG_SPREAD = 0.05
RPE_MAX = 10
#: The WBGT above which heat counts: the optimum for marathon speed lies between 3.8 and
#: 9.9 °C by level (El Helou et al., 2012), cool conditions being WBGT 5-10 °C (Ely et
#: al., 2007).
HEAT_FROM_WBGT_C = 10.0
#: An effect is applied when it stands this many standard errors from zero.
EFFECT_SIGNIFICANCE = 2.0


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
class Conditions:
    """What a run met that its dose does not see; None: not known."""

    #: WBGT degrees above `HEAT_FROM_WBGT_C`, 0 when cooler.
    heat_c: float | None = None
    night: bool | None = None

    def amount(self, condition: Condition) -> float | None:
        """The quantity an effect multiplies: degrees for heat, 0 or 1 for night."""
        if condition is Condition.HEAT:
            return self.heat_c
        return None if self.night is None else float(self.night)


NO_CONDITIONS = Conditions()


@dataclass(frozen=True)
class Effect:
    """What a condition adds to the RPE: per WBGT degree for heat, per run otherwise."""

    condition: Condition
    value: float
    #: Runs that carried the condition in the window.
    sessions: int
    supported: bool


def _effect(condition: Condition, points: Sequence[tuple[float, float]],
            min_sessions: int) -> Effect:
    """The least-squares slope of the residual on the condition's amount, with its
    standard error; for a 0/1 amount it is the difference of the two means."""
    carried = sum(1 for amount, _ in points if amount > 0)
    amounts = [amount for amount, _ in points]
    if carried < min_sessions or len(points) - carried < min_sessions or len(points) < 3:
        return Effect(condition, 0.0, carried, False)
    mean_x = statistics.fmean(amounts)
    mean_y = statistics.fmean(residual for _, residual in points)
    spread = sum((x - mean_x) ** 2 for x in amounts)
    if spread == 0:
        return Effect(condition, 0.0, carried, False)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
    intercept = mean_y - slope * mean_x
    scatter = sum((y - intercept - slope * x) ** 2 for x, y in points) / (len(points) - 2)
    error = math.sqrt(scatter / spread)
    supported = error == 0 or abs(slope) > EFFECT_SIGNIFICANCE * error
    return Effect(condition, slope, carried, supported)


def calibrate(points: Sequence[tuple[Conditions, float]], min_sessions: int) -> tuple[Effect, ...]:
    """The effect of each condition from runs' conditions and RPE residuals."""
    return tuple(_effect(condition, [(amount, residual) for found, residual in points
                                     if (amount := found.amount(condition)) is not None],
                         min_sessions)
                 for condition in Condition)


@dataclass(frozen=True)
class Expectation:
    """The RPE a dose should cost, its range and the history behind it."""

    expected: float
    low: int
    high: int
    sessions: int
    residual: float
    #: The calibrated conditions the run met and what each added to `expected`.
    adjustments: tuple[tuple[Condition, float], ...] = ()

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


def expect(model: Model, dose: Dose, config: Settings,
           adjustments: tuple[tuple[Condition, float], ...] = ()) -> Expectation:
    width = config.width if model.sessions >= config.settled_sessions else config.width_unsettled
    expected = min(max(model.expected(dose) + sum(value for _, value in adjustments), 0.0),
                   float(RPE_MAX))
    low = max(_round(expected - width), 0)
    high = min(_round(expected + width), RPE_MAX)
    return Expectation(expected, low, high, model.sessions, model.residual, adjustments)


def key_of(row: Row) -> tuple[str, str | None]:
    """What a session is calibrated with: its kind, and its routine for strength."""
    return row.type, row.routine if row.type == session_kinds.STRENGTH else None


ProfileOf = Callable[[Row], "easy_load.Profile | None"]
ReferenceOf = Callable[[dt.date], "float | None"]
ConditionsOf = Callable[[Row], Conditions]


def heat_of(row: Row) -> float | None:
    return None if row.wbgt is None else max(row.wbgt - HEAT_FROM_WBGT_C, 0.0)


class History:
    """The ledger's sessions with their doses, ready to calibrate.

    `profile_of` gives the kept streams of a row, `reference_of` the reference easy speed
    on a day (m/s). Each dose is computed once.
    """

    def __init__(self, rows: Iterable[Row], profile_of: ProfileOf, reference_of: ReferenceOf,
                 config: Settings, conditions_of: ConditionsOf | None = None):
        self.config = config
        self.rows = sorted(rows, key=lambda row: row.date)
        self._profile_of, self._reference_of = profile_of, reference_of
        self._conditions_of = conditions_of or (lambda row: Conditions(heat_c=heat_of(row)))
        self._doses: dict[tuple, Dose | None] = {}
        self._bases: dict[tuple, Expectation | None] = {}
        self._effects: dict[dt.date, tuple[Effect, ...]] = {}
        self._conditions: dict[tuple, Conditions] = {}

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

    def conditions(self, row: Row) -> Conditions:
        key = (row.idempotency_key, row.date, row.type)
        if key not in self._conditions:
            self._conditions[key] = (self._conditions_of(row) if row.type in session_kinds.RUN
                                     else NO_CONDITIONS)
        return self._conditions[key]

    def _base(self, row: Row) -> Expectation | None:
        """The expectation of the dose alone, from the sessions before it."""
        key = (row.idempotency_key, row.date, row.type, row.km, row.hours, row.dplus)
        if key not in self._bases:
            dose = self.dose(row)
            found = self.model(key_of(row), row.date) if dose is not None else None
            self._bases[key] = (None if found is None or dose is None
                                else expect(found, dose, self.config))
        return self._bases[key]

    def effects(self, day: dt.date) -> tuple[Effect, ...]:
        """Each condition's effect, calibrated on the runs of the window before `day`."""
        if day not in self._effects:
            since = day - dt.timedelta(weeks=self.config.weeks)
            points = [(self.conditions(row), row.rpe - base.expected) for row in self.rows
                      if since <= row.date < day and row.rpe is not None
                      and row.type in session_kinds.RUN
                      and (base := self._base(row)) is not None]
            self._effects[day] = calibrate(points, self.config.settled_sessions)
        return self._effects[day]

    def expectation(self, row: Row) -> Expectation | None:
        """What a recorded session should have cost, from the sessions before it: its
        dose, then each supported effect of the conditions it met."""
        dose = self.dose(row)
        found = self.model(key_of(row), row.date) if dose is not None else None
        if found is None or dose is None:
            return None
        met = self.conditions(row)
        adjustments = tuple(
            (effect.condition, round(effect.value * amount, 2))
            for effect in (self.effects(row.date) if row.type in session_kinds.RUN else ())
            if effect.supported and (amount := met.amount(effect.condition))
            and round(effect.value * amount, 2))
        return expect(found, dose, self.config, adjustments)

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
    """The history of the ledger with the kept streams of its runs, from the store.

    Streams are read only when a dose or a reference needs them, each run once: the
    reference of a day reads the easy runs of the weeks before it."""
    rows = list(rows)
    profiles: dict[int, easy_load.Profile | None] = {}
    groups: dict[int, bool | None] = {}
    references: dict[dt.date, float | None] = {}
    easy = sorted(((row.date, activity) for row in rows if row.type == session_kinds.EASY
                   for activity in [easy_load.strava_id(row.idempotency_key)]
                   if activity is not None), key=lambda item: item[0])

    def profile(activity: int) -> easy_load.Profile | None:
        if activity not in profiles:
            try:
                profiles[activity] = easy_load.stored_profile(store, activity)
            except Exception:  # unreadable streams: the run reads its raw speed or none
                profiles[activity] = None
        return profiles[activity]

    def profile_of(row: Row) -> easy_load.Profile | None:
        activity = easy_load.strava_id(row.idempotency_key)
        return None if activity is None else profile(activity)

    def reference_of(day: dt.date) -> float | None:
        if day not in references:
            since = day - dt.timedelta(weeks=easy_load.REFERENCE_WEEKS)
            runs = []
            for date, activity in easy:
                shape = profile(activity) if since <= date < day else None
                if shape is None:
                    continue
                if activity not in groups:
                    groups[activity] = easy_load.group_of(store, activity)
                runs.append((date, shape, groups[activity]))
            references[day] = easy_load.reference_mps(easy_load.samples(runs), day)
        return references[day]

    def conditions_of(row: Row) -> Conditions:
        activity = easy_load.strava_id(row.idempotency_key)
        if activity is None:
            return Conditions(heat_c=heat_of(row))
        return Conditions(heat_c=heat_of(row), night=night_of(store, activity))

    return History(rows, profile_of, reference_of, config, conditions_of)


def night_of(store: Any, activity: int) -> bool | None:
    """Whether a captured run spent at least half its time below civil twilight; None
    without its start, its route or a readable instance time zone. The run is read in the
    instance's zone: the athlete's home."""
    from redline.integrations.weather import decode_polyline
    from redline.model import local_time
    from redline.model.activity import Activity

    from . import daylight

    try:
        found = Activity.from_json(store.strava_intake(activity)["distilled"])
        start = dt.datetime.fromisoformat(found.start_local) if found.start_local else None
        points = decode_polyline(found.route_polyline) if found.route_polyline else []
        zone = local_time.zone()
    except Exception:  # no capture, no route or no instance: the night stays unknown
        return None
    if start is None or not points:
        return None
    latitude, longitude = points[0]
    seconds = found.elapsed_s or found.moving_s
    return daylight.dark_share(latitude, longitude, start, seconds, zone) >= 0.5


def above_range(store: Any, rows: Sequence[Row],
                only: Iterable[Row] | None = None) -> dict[tuple, Expectation]:
    """The sessions (of `only`, else all) whose declared RPE is above the range their
    dose expects, by `ledger.row_key`, calibrated on `rows`: what the load gauge weighs
    more on a quality session, and what the weekly review lists. Empty when the rulebook
    declares no method."""
    config = settings()
    if config is None:
        return {}
    found = history(store, rows, config)
    out = {}
    for row in rows if only is None else only:
        if row.rpe is None:
            continue
        expected = found.expectation(row)
        if expected is not None and expected.side(row.rpe) is RpeSide.ABOVE:
            out[row_key(row)] = expected
    return out
