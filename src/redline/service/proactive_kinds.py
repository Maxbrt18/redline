"""How each kind of proactive job behaves, declared once, one row per kind."""

from __future__ import annotations

from dataclasses import dataclass

from redline.model.vocabulary import ProactiveKind as Kind


@dataclass(frozen=True)
class KindPolicy:
    #: How long a job stays relevant after its first failed attempt: past that it is
    #: dropped rather than replayed (a "good morning" in the afternoon is noise).
    staleness_hours: float = 4.0
    #: Runs on the heavy route: a long read of the whole period.
    heavy: bool = False
    #: Starts from an activity that just arrived: there is always something to say, so a
    #: silence is a read failure, retried once, and the activity must end up recorded.
    debrief: bool = False
    #: Must produce a text: staying silent is not an editorial option.
    must_speak: bool = False
    #: Asked for feelings the notebook now collects.
    notebook: bool = False
    #: What the delivered message is recorded as (`proactive_messages.kind`).
    trace: str = "other"


POLICIES: dict[str, KindPolicy] = {
    Kind.STRAVA_DEBRIEF: KindPolicy(debrief=True, notebook=True, trace="debrief"),
    # Already a next-morning message: if the agent is down at 07:30 it still makes sense at
    # 10:00, not the day after.
    Kind.STRAVA_DEBRIEF_LATE: KindPolicy(debrief=True, notebook=True, trace="debrief"),
    Kind.MORNING: KindPolicy(staleness_hours=3.0, notebook=True, trace="morning"),
    Kind.WEEKLY: KindPolicy(staleness_hours=6.0, heavy=True, must_speak=True, trace="weekly"),
    Kind.WEEKLY_REPAIR: KindPolicy(staleness_hours=12.0, heavy=True, must_speak=True),
    Kind.MONTHLY: KindPolicy(staleness_hours=24.0, heavy=True, trace="monthly"),
    Kind.SILENCE: KindPolicy(staleness_hours=24.0),
    # The cell leaves its window at midnight: a claim replayed the next day no longer asks
    # for a memory but for a reconstruction. It dies with the night.
    Kind.LEDGER_INCOMPLETE: KindPolicy(staleness_hours=2.0),
    Kind.MISSED_SESSION: KindPolicy(staleness_hours=8.0),
    Kind.DAILY_SIGNAL: KindPolicy(staleness_hours=12.0),
    Kind.PREMORTEM: KindPolicy(staleness_hours=24.0, heavy=True),
    Kind.CORRECTION: KindPolicy(staleness_hours=24.0),
    Kind.DECISION_CHALLENGE: KindPolicy(staleness_hours=24.0, heavy=True),
    Kind.GUT_REVIEW: KindPolicy(staleness_hours=12.0),
    Kind.GEAR_ALERT: KindPolicy(staleness_hours=24.0),
    Kind.RACE_COUNTDOWN: KindPolicy(staleness_hours=168.0),
    Kind.STRENGTH_CARD: KindPolicy(staleness_hours=12.0),
    Kind.OPERATOR_MESSAGE: KindPolicy(staleness_hours=6.0),
    # The notebook does not take the trace after 14:00: `expires_at` already bounds the job.
    Kind.WAKE_TRACE: KindPolicy(staleness_hours=3.5),
}


def policy(kind: str) -> KindPolicy:
    return POLICIES.get(kind, KindPolicy())
