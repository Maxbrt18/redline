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
    carbs_target_g_h: int


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


#: What a session's line shows once it happened: a run's measures, or a strength report.
#: Nothing tags them; a strength report carries no `observation_id`.
SessionFact = Fact | StrengthFact


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
    fact: SessionFact | None
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


class TraceLevel(TypedDict):
    """A level of the instance's morning-trace scale: the answer sent back is its slug."""

    slug: str
    label: str


class WakeAsk(TypedDict):
    id: str
    type: Literal["wake"]
    zone: str
    label: str
    date: str
    after_effort: bool
    levels: list[TraceLevel]


class CellsAsk(TypedDict):
    id: str
    type: Literal["cells"]
    date: str
    kind: str
    km: float | None
    fields: list[str]
    labels: dict[str, str]
    #: Every cell of the debrief, answered or not: the rank shown is `total - fields + 1`.
    total: int
    #: Bounds the onset of digestive trouble, asked in quarter hours.
    duration_min: int | None
    #: The effort pains offered, from the scale's lowest level to its severe one.
    pain_values: list[float]


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


class DeviceGrant(TypedDict):
    """What `/notebook-api/devices` gives the iOS app: its device token, sent back in
    `Authorization: Bearer` on every later request."""

    device: str


class WeeksView(TypedDict):
    weeks: list[PastWeek]


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
    """A labelled level of the instance's effort-pain scale."""

    value: float
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


class RouteStretch(TypedDict):
    """The climb up to an aid station or the finish, from the previous one."""

    to_km: float
    dplus_m: float
    dminus_m: float


class RouteClimb(TypedDict):
    """A climb of the route, timed at its foot at the plan's reference pace: `hours`
    includes the stops already made, `running_hours` does not."""

    start_km: float
    end_km: float
    gain_m: float
    grade_pct: float
    #: Its steepest section of homogeneous grade: grade and length.
    steepest_pct: float
    steepest_m: float
    #: A climb a runner would name; the others are bumps.
    main: bool
    hours: float
    running_hours: float


class RacePassage(TypedDict):
    """An aid station or the finish (empty name), timed at the plan's reference pace."""

    km: float
    name: str
    hours: float
    running_hours: float


class RacePlan(TypedDict):
    """The race plan of the typed race. Running time is proportional to the pace and the
    stops are not, so the page retimes every passage at any pace from these figures."""

    source: Literal["ign", "gpx"]
    length_km: float
    dplus_m: float
    dminus_m: float
    dplus_band_m: list[float] | None
    #: [km, m] every ~200 m, and the high point as [m, km].
    profile: list[list[float]]
    highest: list[float]
    stretches: list[RouteStretch]
    passages: list[RacePassage]
    climbs: list[RouteClimb]
    reference_pace_s: float
    #: The pace chosen with the coach; None: no passage is shown.
    pace_s_per_km: float | None
    stop_minutes: float
    carbs_g_h: int | None
    #: The relative bands: the median error, then nine times out of ten.
    model_error: list[float]
    #: Local start, YYYY-MM-DDTHH:MM; None without a start time.
    start: str | None


class PassageHour(TypedDict):
    """One hour of forecast at a passage's place; `time` is local, YYYY-MM-DDTHH:00."""

    time: str
    temp: int
    feels: int
    rain: int | None
    wind: int
    code: int | None
    day: bool


class RacePlanView(TypedDict):
    plan: RacePlan
    #: For each passage, the hours of the race night at its place; None until the
    #: forecast reaches the race date.
    weather: list[list[PassageHour]] | None


class RaceView(TypedDict):
    race: Race | None
    plan: RacePlan | None
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
    total: int
    duration_min: int | None
    pain_values: list[float]


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


class ReorganizeSession(TypedDict):
    """A session the plan holds on a day, as the reorganisation screen lists it."""

    id: str
    title: str
    kind: str
    workout: str
    #: The binding figure, short: "≤50 min", "25 km".
    figure: str


class DayState(TypedDict):
    """How a day presents itself, as stated or as its weekly habit gives it."""

    kind: Literal["as_planned", "off", "short", "easy", "more", "with_others", "other_sport"]
    minutes: int | None
    pace: Literal["easy", "mixed", "workout"] | None
    required: bool | None
    sport: str | None
    demanding: bool | None
    run_too: bool | None
    #: It comes from the habit of that weekday.
    habit: bool


class ReorganizeDay(TypedDict):
    date: str
    sessions: list[ReorganizeSession]
    #: What is already known of the day: stated for it, else its habit.
    stated: DayState | None
    #: The habit in force for that weekday on that date.
    habit_id: str | None
    #: The plan keeps the day for rest.
    rest: bool
    #: Before today: shown, never moved.
    past: bool


class VariantMove(TypedDict):
    session: str
    title: str
    before: str
    after: str


class VariantDrop(TypedDict):
    session: str
    title: str
    #: The distance the week loses, when the session states one.
    km: float | None


class VariantNote(TypedDict):
    """A preference the variant bends; the page words it from `remark`."""

    remark: Literal["impact_near_hard", "load_above_plan", "long_off_days",
                    "group_uncarried", "rest_day_used"]
    title: str | None
    date: str | None
    value: int | None


class VariantDay(TypedDict):
    date: str
    #: The short labels of what the day holds, in order.
    labels: list[str]
    #: The day's planned load, on the scale of the week.
    load: float
    moved: bool
    carried: bool


class Variant(TypedDict):
    moves: list[VariantMove]
    dropped: list[VariantDrop]
    notes: list[VariantNote]
    days: list[VariantDay]
    around_min: int | None


class Proposal(TypedDict):
    """A revision the coach proposed for the week, taken with one tap."""

    id: str
    title: str
    body: str
    #: What it changes, one line per session, in the coach's words.
    lines: list[str]


class ReorganizeView(TypedDict):
    """The athlete's week to reorganise, and the variants the days they stated allow.

    `blocked` names why nothing can move: a dated check awaits an answer, the plan is
    missing, or the week is over."""

    week: str
    revision: int
    #: False while the coach has not written the week: the days are stated for it.
    written: bool
    blocked: Literal["awaiting", "missing", "closed"] | None
    days: list[ReorganizeDay]
    variants: list[Variant]
    others: int
    #: Titles of the demanding sessions a pain signal keeps from moving earlier.
    held_back: list[str]
    #: What the coach proposed for this revision of the week, if the athlete asked.
    proposals: list[Proposal]


class Habit(DayState):
    id: str
    weekday: int
    starts_on: str
    ends_on: str | None


class HabitsView(TypedDict):
    habits: list[Habit]
