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
