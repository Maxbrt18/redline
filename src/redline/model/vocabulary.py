"""The closed vocabularies stored in PostgreSQL, declared once.

Each value is an English slug: the schema's CHECK constraints, the CLIs' choices and the
notebook read them from here. What the athlete reads is a French label, looked up in
`redline.coach.fr`.
"""

from __future__ import annotations

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


class MorningTrace(StrEnum):
    """What a session left the next morning, in the athlete's own scale."""

    NONE = "none"
    VERY_LIGHT = "very-light"
    LIGHT = "light"
    CLEAR = "clear"


#: The pain value each morning trace is recorded as.
MORNING_TRACE_PAIN: dict[str, float] = {
    MorningTrace.NONE: 0, MorningTrace.VERY_LIGHT: 0.5, MorningTrace.LIGHT: 1,
    MorningTrace.CLEAR: 2,
}


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


def sql_values(vocabulary: type[StrEnum]) -> str:
    """`'a','b'`, for a CHECK constraint."""
    return ",".join(f"'{member.value}'" for member in vocabulary)
