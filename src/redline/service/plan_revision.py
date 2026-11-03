"""The checks a new revision passes before it is written, whoever writes it.

The coach's command and the notebook write revisions through the same gate: the
engine's clock, the decision state measured at that instant, every write-time rule, then
the whole plan validated with the new revision appended.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from redline.analysis import decision_state
from redline.coach.fr import plan as wording
from redline.model.schema import SchemaError
from redline.model.weekly_plan import (
    change_evidence_errors,
    decision_errors,
    fuelling_errors,
    intention_errors,
    typed_target_errors,
    validate,
    written_text_errors,
)


def taper_errors(store, week: str, revision: dict, warn: Callable[[str], None]) -> list[str]:
    """The taper band, when `week` is in one. A measurement that fails costs the check,
    never the prescription."""
    from redline.analysis import ledger, taper
    try:
        frame = taper.race_window(store, week)
        rows = ledger.parse_ledger(store) if frame else []
    except Exception as exc:
        warn(wording.TAPER_UNREADABLE.format(name=exc.__class__.__name__))
        return []
    if frame is None:
        return []
    return taper.plan_errors(rows, frame, week, revision,
                             f"{week} revision {revision['revision']}")


def checked(store, week: str, revision: dict, existing: dict | None, *, stamp: str,
            warn: Callable[[str], None]) -> tuple[dict | None, dict]:
    """The previous revision and the new one, stamped and measured, or SchemaError."""
    expected = 1 if existing is None else len(existing["revisions"]) + 1
    if revision.get("revision") != expected:
        raise SchemaError([f"revision: expected {expected}, got {revision.get('revision')!r}"])
    current = dict(revision)
    # The timestamp belongs to the service, not the model. A model that invents the time
    # of day also invents the day the athlete is talking about, and `semantic_errors`
    # compares a dated check's due date to this field, so a wrong time would disarm a
    # guardrail. The only reliable clock in the chain is the one that writes.
    current["computed_at"] = stamp
    # What the coach could see is measured by the engine, at the same instant. A
    # measurement that fails costs the register one snapshot, never the prescription.
    current.pop("state_snapshot", None)
    try:
        current["state_snapshot"] = decision_state.snapshot(
            store, dt.date.fromisoformat(current["computed_at"][:10]))
    except Exception as exc:
        warn(wording.SNAPSHOT_UNREADABLE.format(name=exc.__class__.__name__))
    # The incoming text is judged before the plan containing it: the revision is fed back
    # to the coach on every prescriptive turn, and title/note go to the athlete's phone.
    # Judged here and not in `validate` because history is append-only: the same rule
    # applied to the whole plan would refuse today's revision because of yesterday's text.
    previous = existing["revisions"][-1] if existing else None
    where = f"{week} revision {current['revision']}"
    mistakes = [
        *intention_errors(current, where),
        *written_text_errors(current, where),
        *change_evidence_errors(previous, current),
        *typed_target_errors(current, where),
        *decision_errors(current, where),
        *fuelling_errors(current, where),
        *taper_errors(store, week, current, warn),
    ]
    if mistakes:
        raise SchemaError(mistakes)
    validate({"week": week, "revisions": [*(existing or {}).get("revisions", []), current]})
    return previous, current
