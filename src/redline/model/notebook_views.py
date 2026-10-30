"""The JSON the notebook page reads: one declared shape per screen.

`Board` and `NotebookService` build these objects and the page reads them, so this module
is the one description of what crosses between Python and JavaScript. They are TypedDicts
rather than dataclasses because what travels is a JSON object, where an absent key
(`NotRequired`) is not a null. mypy holds the builders to these shapes;
tests/test_notebook_contract.py holds the served JSON, the browser fixtures and the page's
modules to them.

This module skips `from __future__ import annotations`: a TypedDict reads `NotRequired`
when the class is created, and a string annotation would hide it from `__required_keys__`.
"""

from typing import Any, Literal, NotRequired, TypedDict


class Targets(TypedDict, total=False):
    """The typed limits of a session; a limit the plan does not set is absent."""

    distance_target_km: float
    distance_cap_km: float
    duration_cap_min: int
    rpe_min: int
    rpe_max: int
    hr_cap_bpm: int


class PainReading(TypedDict):
    zone: str
    label: str
    value: float


class Fact(TypedDict):
    """The known measures of an observation, and the typed caps they exceed."""

    observation_id: str
    kind: str
    date: str
    km: float | None
    duration_min: int | None
    hr_avg: float | None
    rpe: float | None
    pains: list[PainReading]
    enjoyment: float | None
    dplus: float | None
    started_at: str | None
    over: list[str]


class StrengthFact(TypedDict):
    """A strength report, which carries none of a run's measures."""

    duration_min: int | None
    pains: list[PainReading]
    over: list[str]


class UnplannedFact(Fact):
    """What was run or walked with no program session to carry it."""

    title: str


class SessionItem(TypedDict):
    id: str
    date: str
    title: str
    kind: str
    workout: str
    status: str
    start_time: str | None
    location: str | None
    note: str | None
    planned: list[str]
    fact: Fact | StrengthFact | None
    entry_id: str | None
    transition: str | None
    targets: Targets
    change: str | None


class StripDay(TypedDict):
    date: str
    letter: str
    abbr: str
    today: bool
    done_km: float | None
    planned_km: float | None
    planned_label: str | None
    long: bool
    rest: bool


class PainPeak(TypedDict):
    zone: str
    label: str
    peak: float | None


class Totals(TypedDict):
    km_done: float
    km_target: float | None
    runs_done: int
    runs_planned: int
    dplus: float
    strength_done: int
    strength_planned: int
    load: int
    pain_peaks: list[PainPeak]


class PainWeek(TypedDict):
    week: str
    peak: float | None


class PainTrendZone(TypedDict):
    zone: str
    label: str
    weeks: list[PainWeek]


class PainTrend(TypedDict):
    cap: float
    zones: list[PainTrendZone]


class Race(TypedDict):
    name: str
    date: str
    days: int
    start_time: NotRequired[str]
    distance_km: NotRequired[float]
    elevation_gain_m: NotRequired[float]
    elevation_loss_m: NotRequired[float]
    elevation_approximate: NotRequired[bool]
    target: NotRequired[str]


class WakeAsk(TypedDict):
    id: str
    type: Literal["wake"]
    zone: str
    label: str
    date: str
    after_effort: bool


class CellsAsk(TypedDict):
    id: str
    type: Literal["cells"]
    date: str
    kind: str
    km: float | None
    fields: list[str]
    labels: dict[str, str]


class DailyAsk(TypedDict):
    id: str
    type: Literal["daily"]
    date: str
    fields: list[str]


class HrAsk(TypedDict):
    id: str
    type: Literal["hr"]
    date: str
    hr: int
    reference: int
    n: int
    pace_s: int


Ask = WakeAsk | CellsAsk | DailyAsk | HrAsk


class StrengthClosed(TypedDict):
    """Why no guided session is offered today."""

    status: Literal["unavailable", "none", "blocked", "done"]
    date: str
    message: str


class StrengthReady(TypedDict):
    """The prescription snapshot belongs to the strength session, which has its own reader."""

    status: Literal["ready"]
    date: str
    session: dict[str, Any]
    revision: int
    signature: str


StrengthOffer = StrengthClosed | StrengthReady


class PlanNote(TypedDict):
    status: str
    message: str


class Tomorrow(TypedDict):
    date: str
    plan: str
    sessions: list[SessionItem]
    next: list[SessionItem]


class BoardToday(TypedDict):
    date: str
    week: str
    plan: PlanNote
    sessions: list[SessionItem]
    extra: list[UnplannedFact]
    tomorrow: Tomorrow
    strip: list[StripDay]
    totals: Totals
    asks: list[Ask]
    ahead: list[SessionItem]
    pain: PainTrend
    race: Race | None


class TodayView(BoardToday):
    strength: StrengthOffer


class Entry(TypedDict):
    """An archived card, as listed."""

    id: str
    kind: str
    title: str
    period: str
    occurred_on: str
    created_at: str
    origin: str
    has_image: bool


class WeekPlan(PlanNote):
    revision: int | None


class NextWeek(TypedDict):
    week: str
    monday: str
    km_target: float | None
    runs_planned: int


class WeekView(TypedDict):
    week: str
    monday: str
    current: str
    plan: WeekPlan
    sessions: list[SessionItem]
    extra: list[UnplannedFact]
    totals: Totals
    strip: list[StripDay]
    review: Entry | None
    cards: list[Entry]
    next: NextWeek | None


class PastWeek(TypedDict):
    week: str
    monday: str
    km_done: float
    km_target: float | None
    review: str | None


class ZoneName(TypedDict):
    zone: str
    label: str


class InjuryPoint(TypedDict):
    date: str
    pain: float
    kind: str
    long: bool


class WakePoint(TypedDict):
    date: str
    pain: float


class InjuryWeek(TypedDict):
    week: str
    peak: float | None
    n: int
    wake: float | None


class ScaleStep(TypedDict):
    value: int
    text: str


class InjuryView(TypedDict):
    zone: str
    label: str
    zones: list[ZoneName]
    cap: float
    start: str
    today: str
    week: str
    points: list[InjuryPoint]
    wakes: list[WakePoint]
    weeks: list[InjuryWeek]
    missing: list[str]
    scale: list[ScaleStep]


class LongestWeek(TypedDict):
    week: str
    km: float
    volume: float
    target: float | None
    open: bool


class Longest(TypedDict):
    km: float | None
    weeks: list[LongestWeek]
    planned_km: float | None
    planned_on: str | None


class SessionMilestone(TypedDict):
    date: str
    label: str
    kind: Literal["session"]
    km: float


class DeadlineMilestone(TypedDict):
    date: str
    label: str
    kind: Literal["deadline"]
    overdue: bool


class RaceMilestone(TypedDict):
    date: str
    label: str
    kind: Literal["race"]
    start_time: str | None


Milestone = SessionMilestone | DeadlineMilestone | RaceMilestone


class Capability(TypedDict):
    label: str
    icon: str
    score: int
    scale: int
    number: int
    evaluated_on: str


class RaceView(TypedDict):
    race: Race | None
    longest: Longest
    milestones: list[Milestone]
    capabilities: list[Capability]
    archives: list[Entry]


class DetailFact(Fact):
    moving_s: float | None
    hr_max: float | None
    cadence: float | None
    reserve: float | None


class DetailPlan(TypedDict):
    targets: Targets
    note: str | None


class DetailAsk(TypedDict):
    id: str
    fields: list[str]
    labels: dict[str, str]


class WakeZone(TypedDict):
    zone: str
    label: str
    before: float | None
    after: float | None


class WakeTrace(TypedDict):
    """The wake trace before the session and the next morning, per zone."""

    zones: list[WakeZone]
    after_due: bool


class Effort(TypedDict):
    time_s: float
    dist_m: int
    pace_s: int | None
    hr: int | None
    cadence: int | None
    dplus: int


class Phase(Effort):
    kind: Literal["warmup", "rep", "recovery", "cooldown"]
    start: float
    end: float


class Rep(Phase):
    n: int
    hr_drop: int | None


class RepReading(TypedDict):
    last: int
    fastest_before: int
    slowest_before: int
    final_acceleration: bool


class Quality(TypedDict):
    phases: list[Phase]
    reps: list[Rep]
    reading: RepReading | None


class KmSplit(TypedDict):
    n: int
    dist_m: int
    moving_s: int
    pace_s: int
    hr: int | None
    cadence: int | None
    dplus: int


class StopSummary(TypedDict):
    count: int
    total_s: int
    longest_s: int
    longest_km: int | None


class Drift(TypedDict):
    first: int
    second: int


class Curves(TypedDict):
    t: list[int]
    duration: int
    hr: list[int | None] | None
    alt: list[float | None] | None


class Series(Curves):
    bands: list[list[float]]
    stops: list[list[int]]


class ZoneSeconds(TypedDict):
    max: int | None
    seconds: list[int]


class Previous(TypedDict):
    """The last session of the same type, for comparison."""

    date: str
    km: float | None
    hr_avg: float | None
    rpe: float | None
    pains: list[PainReading]
    enjoyment: float | None
    rep_dplus: int | None
    rep_hr: int | None


class Conditions(TypedDict, total=False):
    """The weather of a run; a capture without a reading only knows its WBGT."""

    temp: float | None
    feels_like: float | None
    humidity: float | None
    wind: float | None
    wbgt: float | None
    conditions: str


class Gear(TypedDict):
    name: str
    km: int
    retire_at_km: float | None
    status: str | None


class NextDay(TypedDict):
    date: str
    titles: list[str]
    families: list[list[str]]
    planned: list[str]


class WeekContext(TypedDict):
    week: str
    current: bool
    km_done: float
    km_target: float | None
    runs_done: int
    runs_planned: int
    next: NextDay | None


class SessionView(TypedDict):
    id: str
    date: str
    kind: str
    title: str | None
    workout: str | None
    quality: bool
    strava_id: int | None
    fact: DetailFact
    plan: DetailPlan | None
    ask: DetailAsk | None
    reserve_ask: str | None
    wake: WakeTrace
    phases: list[Phase] | None
    reps: list[Rep] | None
    reading: RepReading | None
    km: list[KmSplit] | None
    stops: StopSummary | None
    drift: Drift | None
    series: Series | None
    hr_bounds: list[int] | None
    zones: ZoneSeconds | None
    previous: Previous | None
    weather: Conditions | None
    gear: Gear | None
    week: WeekContext
