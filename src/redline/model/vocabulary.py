"""The closed vocabularies stored in PostgreSQL, declared once.

Each value is an English slug: the schema's CHECK constraints, the CLIs' choices and the
notebook read them from here. What the athlete reads is a French label, looked up in
`redline.coach.fr`.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Sleep(StrEnum):
    """How a night felt (`daily_checkins.sleep`)."""

    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class LifeLoad(StrEnum):
    """The day's load outside running (`daily_checkins.life_load`)."""

    LIGHT = "light"
    NORMAL = "normal"
    HIGH = "high"


class Desire(StrEnum):
    """The wish to run (`daily_checkins.desire`)."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class WeekSleep(StrEnum):
    """A week's sleep as the load gauge weighs it (`weekly_instruments.sleep`)."""

    OK = "ok"
    BAD_NIGHTS = "bad_nights"
    BAD_WEEK = "bad_week"


class WeekLifeLoad(StrEnum):
    """A week's life load as the load gauge weighs it (`weekly_instruments.life_load`)."""

    NORMAL = "normal"
    HIGH = "high"


class HrDoubt(StrEnum):
    """Why an average heart rate is doubtful (`observation_metrics.hr_doubt`).

    `no_sensor` invalidates it everywhere; `context` keeps it as a fact but out of the
    pace/HR comparisons.
    """

    NO_SENSOR = "no_sensor"
    CONTEXT = "context"


class Exposure(StrEnum):
    """What a session exposed the body to (`session_exposures.exposure_type`)."""

    CADENCE = "cadence"
    DESCENT = "descent"
    STRIDES = "strides"
    NUTRITION = "nutrition"
    GEAR = "gear"
    OTHER = "other"


class CapabilityStatus(StrEnum):
    """A capability's light (`capability_evaluations.status`)."""

    RED = "red"
    AMBER = "amber"
    GREEN = "green"


class ThreadKind(StrEnum):
    """What a conversation thread holds (`conversation_threads.kind`)."""

    PROMISE = "promise"
    FEELING = "feeling"
    OPEN_LOOP = "open_loop"


class ThreadStatus(StrEnum):
    """`conversation_threads.status`."""

    OPEN = "open"
    CLOSED = "closed"


class HrReason(StrEnum):
    """The explanation the athlete gives the notebook for a high heart rate."""

    SLEEP = "sleep"
    COLD = "cold"
    HEAT = "heat"
    NONE = "none"


class ElevationSource(StrEnum):
    """Where the elevation of a race route profile comes from (`analysis.route_profile`)."""

    IGN = "ign"
    GPX = "gpx"


class CarbMix(StrEnum):
    """The sugars a fuelling target is taken as (`planned_sessions.carbs_mix`), and the
    sugars a session's fuel was actually taken as (`observation_metrics.fuel_mix`)."""

    GLUCOSE = "glucose"
    GLUCOSE_FRUCTOSE = "glucose_fructose"


class FuelFormat(StrEnum):
    """What a session's carbohydrate came in (`observation_metrics.fuel_formats`)."""

    DRINK = "drink"
    GEL = "gel"
    CHEW = "chew"
    SOLID = "solid"


class GutSymptom(StrEnum):
    """The kinds of digestive trouble felt during a session
    (`observation_metrics.gut_symptom_kinds`)."""

    NAUSEA = "nausea"
    REFLUX = "reflux"
    CRAMPS = "cramps"
    BLOATING = "bloating"
    URGENCY = "urgency"


class Decider(StrEnum):
    """What settled a plan revision (`plan_revisions.decision`).

    A rule leaves no choice and identifies nothing; a judgment weighed options and must
    name the ones it set aside.
    """

    RULE = "rule"
    JUDGMENT = "judgment"
    REQUEST = "request"


class DayKind(StrEnum):
    """How a day presents itself to the athlete when the week is reorganised.

    Named by what the athlete can do that day, never by its cause.
    """

    AS_PLANNED = "as_planned"
    OFF = "off"
    SHORT = "short"
    EASY = "easy"
    MORE = "more"
    WITH_OTHERS = "with_others"
    OTHER_SPORT = "other_sport"


class GroupPace(StrEnum):
    """How a group the athlete runs with moves: what kind of session it can carry."""

    EASY = "easy"
    MIXED = "mixed"
    WORKOUT = "workout"


class SessionPriority(StrEnum):
    """What a planned session weighs when days run short (`priority` of a session).

    A key session is never dropped by a reorganisation; an optional one goes first.
    """

    KEY = "key"
    SUPPORT = "support"
    OPTIONAL = "optional"


class ClimbIntent(StrEnum):
    """What a planned run asks of its climbs (`climb_intent`): what the debrief judges
    there. The HR cap never applies on a climb; the intent says what does."""

    #: Easy effort, walking allowed: only the flat is judged.
    EASY = "easy"
    #: Every climb run, as slowly as needed; the HR may rise. A walked climb is named.
    RUN_SLOW = "run_slow"
    #: Climbs at race effort: the effort's regularity from one climb to the next.
    RACE_EFFORT = "race_effort"
    #: The climbs are the repetitions: the rules of a quality session.
    HARD = "hard"
    #: Brisk walking from a grade on (`power_hike_from_pct`), easy running below.
    POWER_HIKE = "power_hike"


class DescentIntent(StrEnum):
    """What a planned run asks of its descents (`descent_intent`)."""

    #: Free descents, under the descent cap.
    RELAXED = "relaxed"
    #: Held or walked: clearly slower than the flat easy pace.
    HELD = "held"
    #: A descent dose: its loss and the next morning's trace.
    WORKED = "worked"


class SessionStructure(StrEnum):
    """Whether an easy or long run is run steady or carries prescribed efforts
    (`structure`): a pyramid, strides, a structure set by the group."""

    STEADY = "steady"
    EFFORTS = "efforts"


class DebriefVerdict(StrEnum):
    """What the service rules on a run's execution against its typed prescription
    (`strava_activity_intakes.debrief_verdict`), before the coach writes."""

    #: The run followed its prescription: nothing to correct.
    CONFORMING = "conforming"
    #: A choice of the athlete, during the run or of its route, went past the prescription.
    EXECUTION_GAP = "execution_gap"
    #: No prescription or no reading to judge against.
    UNDETERMINED = "undetermined"


class FindingKind(StrEnum):
    """One reading of a run against its prescription (`debrief_findings`)."""

    #: Flat HR above the cap at a pace faster than the reference easy pace.
    FLAT_TOO_FAST = "flat_too_fast"
    #: Flat HR above the cap at the reference easy pace or slower: a state or sensor sign.
    FLAT_HR_HIGH = "flat_hr_high"
    DPLUS_OVER = "dplus_over"
    DMINUS_OVER = "dminus_over"
    #: A climb walked on a run whose climbs were to be run.
    CLIMB_WALKED = "climb_walked"
    #: A grade past the power-hike threshold run instead of walked.
    STEEP_RUN = "steep_run"
    #: The share of the grades past the power-hike threshold that was walked.
    STEEP_WALKED_SHARE = "steep_walked_share"
    #: A descent run faster than the flat easy pace on a run whose descents were held.
    DESCENT_NOT_HELD = "descent_not_held"


class FindingOwner(StrEnum):
    """Whose a finding is: what the coach does with it."""

    #: A choice made during the run: it receives the correction.
    ATHLETE = "athlete"
    #: A choice made before leaving, the route: named as such, apart from the HR.
    ROUTE = "route"
    #: The body's answer: a state or sensor sign, at most a question, never a reproach.
    BODY = "body"
    #: A fact to name, without reproach.
    NOTE = "note"


class ProactiveKind(StrEnum):
    """What a proactive job does (`proactive_jobs.kind`)."""

    STRAVA_DEBRIEF = "strava_debrief"
    STRAVA_DEBRIEF_LATE = "strava_debrief_late"
    SILENCE = "silence"
    LEDGER_INCOMPLETE = "ledger_incomplete"
    MORNING = "morning"
    WEEKLY = "weekly"
    WEEKLY_REPAIR = "weekly_repair"
    MONTHLY = "monthly"
    SESSION_FOLLOWUP = "session_followup"
    MISSED_SESSION = "missed_session"
    DAILY_SIGNAL = "daily_signal"
    PREMORTEM = "premortem"
    CORRECTION = "correction"
    DECISION_CHALLENGE = "decision_challenge"
    GUT_REVIEW = "gut_review"
    GEAR_ALERT = "gear_alert"
    RACE_COUNTDOWN = "race_countdown"
    STRENGTH_CARD = "strength_card"
    CALIBRATION_EVE = "calibration_eve"
    OPERATOR_MESSAGE = "operator_message"
    WAKE_TRACE = "wake_trace"


class SessionStatus(StrEnum):
    """A planned session's status in a revision (`planned_sessions.status`).

    `done` is never written: it is read from the session cycle and kept by old revisions.
    """

    PLANNED = "planned"
    CANCELLED = "cancelled"
    DONE = "done"


#: What a new revision may write: `done` is never planned, it happens.
INTENT_STATUSES = (SessionStatus.PLANNED, SessionStatus.CANCELLED)
#: The statuses that settle a session: it happened or it never will.
SETTLED_STATUSES = frozenset({SessionStatus.DONE, SessionStatus.CANCELLED})


class CycleState(StrEnum):
    """Where a planned session stands in its life (`session_cycles.state`)."""

    PLANNED = "planned"
    STARTED = "started"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"
    FOLLOWUP_DUE = "followup_due"
    CLOSED = "closed"


class MeasurementState(StrEnum):
    """Whether a measured value is known yet (`observation_measurements.state`)."""

    PENDING = "pending"
    KNOWN = "known"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class EnrichmentState(StrEnum):
    """Where a Strava enrichment stands (`strava_activity_intakes.*_state`)."""

    PENDING = "pending"
    KNOWN = "known"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class IntakeStatus(StrEnum):
    """What became of a captured Strava activity (`strava_activity_intakes.status`)."""

    CAPTURED = "captured"
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"
    FAILED = "failed"


class ExposureOrigin(StrEnum):
    """Why an exposure happened (`exposures.origin`)."""

    PRESCRIBED = "prescribed"
    VOLUNTARY = "voluntary"
    SPONTANEOUS = "spontaneous"


class ExposureStatus(StrEnum):
    """Whether an exposure is established (`exposures.status`)."""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class Tolerance(StrEnum):
    """How an exposure was absorbed (`exposures.tolerance`)."""

    UNKNOWN = "unknown"
    EASY = "easy"
    STRAINED = "strained"
    PROTECTIVE = "protective"


class QuestionStatus(StrEnum):
    """Where a question asked to the athlete stands (`questions.status`)."""

    OPEN = "open"
    DEFERRED = "deferred"
    ANSWERED = "answered"
    EXPIRED = "expired"


class DeadlineStatus(StrEnum):
    """Where a deadline stands (`deadlines.status`)."""

    TODO = "todo"
    SCHEDULED = "scheduled"
    DONE = "done"


class DeadlineKind(StrEnum):
    """What a deadline is about (`deadlines.kind`)."""

    GENERAL = "general"
    MEDICAL = "medical"
    AVAILABILITY = "availability"
    RACE = "race"


class GearStatus(StrEnum):
    """Whether a piece of gear is still worn (`gear_items.status`)."""

    ACTIVE = "active"
    RETIRED = "retired"


class Direction(StrEnum):
    """Which way a prediction expects the load to move (`predictions.direction`)."""

    INCREASE = "increase"
    DECREASE = "decrease"
    NEUTRAL = "neutral"


class JobStatus(StrEnum):
    """Where a proactive job stands (`proactive_jobs.status`)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


#: The jobs still to run or running.
LIVE_JOBS = (JobStatus.PENDING, JobStatus.RUNNING)
#: The jobs that will never run again unless reopened.
FINISHED_JOBS = (JobStatus.DONE, JobStatus.FAILED)


class InboundStatus(StrEnum):
    """Whether an inbound message has been answered (`inbound_messages.status`)."""

    PENDING = "pending"
    DONE = "done"


class DeliveryStatus(StrEnum):
    """Where an outbound envelope stands (`outbound_deliveries.status`)."""

    PREPARED = "prepared"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"


#: The envelopes that may be sent again: never started, or failed for certain.
RESENDABLE_DELIVERIES = (DeliveryStatus.PREPARED, DeliveryStatus.FAILED)


def sql_values(members: Iterable[StrEnum]) -> str:
    """`'a','b'`, for a CHECK constraint: a whole vocabulary, or some of its members."""
    return ",".join(f"'{member}'" for member in members)
