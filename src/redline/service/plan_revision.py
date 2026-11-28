"""The checks a new revision passes before it is written, whoever writes it.

The coach's command and the notebook write revisions through the same gate: the
engine's clock, the decision state measured at that instant, every write-time rule, then
the whole plan validated with the new revision appended.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from sqlalchemy.exc import SQLAlchemyError

from redline.analysis import decision_state
from redline.coach.fr import plan as wording
from redline.model import prescription
from redline.model.errors import ExternalError, RedlineError, StorageError
from redline.model.schema import SchemaError
from redline.model.weekly_plan import (
    change_evidence_errors,
    decision_errors,
    easy_bound_errors,
    fuelling_errors,
    intention_errors,
    pain_rule_errors,
    rpe_band_errors,
    typed_target_errors,
    validate,
    written_text_errors,
)

#: A measurement that cannot be taken: a read of the store or of a service that fails. It
#: costs the check, with a warning, never the prescription.
UNAVAILABLE = (StorageError, ExternalError, OSError, SQLAlchemyError)


def _guarded(name: str, where: str, unavailable: str, warn: Callable[[str], None],
             check: Callable[[], list[str]]) -> list[str]:
    """The refusals of one guard. Only an unavailable measurement lets the revision through
    unchecked, with a warning; a declared frame that cannot be read refuses, and so does a
    failure of the check itself."""
    try:
        return check()
    except UNAVAILABLE as exc:
        warn(unavailable.format(name=exc.__class__.__name__))
        return []
    except RedlineError as exc:
        return [f"{where}: the {name} check cannot read its frame: {exc}"]
    # A guard that fails on a defect refuses: no revision passes a check that was not
    # made, and the refusal names the failure so the coach can report it.
    except Exception as exc:
        return [f"{where}: the {name} check failed ({exc.__class__.__name__}: {exc}); "
                "the revision waits for it to be fixed"]


def _where(week: str, revision: dict) -> str:
    return f"{week} revision {revision.get('revision')}"


def taper_errors(store, week: str, revision: dict, warn: Callable[[str], None]) -> list[str]:
    """The taper band, when `week` is in one."""
    from redline.analysis import ledger, taper

    def check() -> list[str]:
        frame = taper.race_window(store, week)
        if frame is None:
            return []
        return taper.plan_errors(ledger.parse_ledger(store), frame, week, revision,
                                 _where(week, revision))

    return _guarded("taper", _where(week, revision), wording.TAPER_UNREADABLE, warn, check)


def pain_errors(store, week: str, revision: dict, existing: dict | None,
                warn: Callable[[str], None]) -> list[str]:
    """Rules n3 and n4, from the ledger at the instant of writing."""
    from redline.analysis import ledger, pain_rules
    today = dt.date.fromisoformat(revision["computed_at"][:10])

    def check() -> list[str]:
        rows = [row for row in ledger.parse_ledger(store) if row.date <= today]
        zones = [zone.slug for zone in ledger.pain_zones(store)]
        holds = pain_rules.holds(rows, store.daily_rows(), zones, today)
        ceilings = pain_rules.volume_ceilings(
            rows, zones, week, (existing or {}).get("revisions", []), today)
        previous = existing["revisions"][-1] if existing else None
        return pain_rule_errors(revision, holds, ceilings, _where(week, revision), previous)

    return _guarded("pain rules", _where(week, revision), wording.PAIN_RULES_UNREADABLE, warn,
                    check)


def progression_errors(store, week: str, revision: dict, previous: dict | None,
                       warn: Callable[[str], None]) -> list[str]:
    """The quality doses against their lineages' ceilings, the bounded override at most."""
    from redline.analysis import progression_runs
    from redline.model import rulebook
    today = dt.date.fromisoformat(revision["computed_at"][:10])

    def check() -> list[str]:
        return progression_runs.override_errors(store, rulebook.current(), week, revision,
                                                previous, today)

    return _guarded("progression", _where(week, revision), wording.PROGRESSION_UNREADABLE,
                    warn, check)


def gut_errors(store, week: str, revision: dict, warn: Callable[[str], None]) -> list[str]:
    """The carbohydrate targets against the gut-training lineage, when one is declared."""
    from redline.analysis import progression_fuelling
    from redline.model import rulebook
    today = dt.date.fromisoformat(revision["computed_at"][:10])

    def check() -> list[str]:
        return progression_fuelling.ceiling_errors(store, rulebook.current(), week, revision,
                                                   today)

    return _guarded("gut training", _where(week, revision), wording.GUT_UNREADABLE, warn,
                    check)


def _overridden(revision: dict | None) -> frozenset:
    """What a revision overrode, by target and by the workout of the session it names."""
    from redline.model.vocabulary import OverrideTarget
    if revision is None:
        return frozenset()
    workouts = {session["id"]: session.get("workout") or ""
                for session in revision.get("sessions") or []}
    return frozenset((OverrideTarget(item["target"]),
                      workouts.get(item.get("session") or "", "") if item.get("session")
                      else "")
                     for item in revision.get("cycle_overrides") or [])


def cycle_errors(store, week: str, revision: dict, previous: dict | None,
                 warn: Callable[[str], None]) -> list[str]:
    """The week against the cycle version that holds it. A week before the first version
    has none and is free; a later week no version holds is refused."""
    from redline.analysis import cycle_report
    from redline.model import cycle_gate, cycle_templates, iso_week, paths
    from redline.model import race as race_model
    where = _where(week, revision)

    def check() -> list[str]:
        version = store.cycle_for_week(week)
        if version is None:
            first = store.first_cycle_week()
            if first is None or week < first:
                return []
            return [f"{where}: no cycle version holds the week (`redline cycle show`); the "
                    "planner writes the next one on the last day of the one in force"]
        bound = version.week(week)
        assert bound is not None, "the version holding a week has it"
        goal = race_model.active_goal(store)
        templates = cycle_templates.load(paths.vault_root())
        before = store.load_plan(iso_week.shift(week, -1))
        frame = cycle_gate.Frame(
            version, bound, templates.key_sessions(version.kind, version.trame.template,
                                                   bound.phase),
            cycle_report.references(store, goal.goal if goal is not None else None),
            frozenset(row["prediction_key"] for row in store.prediction_rows()
                      if row.get("status") == "open"),
            _overridden(before["revisions"][-1] if before else None))
        return cycle_gate.errors(frame, revision, previous)

    return _guarded("cycle", where, wording.CYCLE_UNREADABLE, warn, check)


def draft_errors(store, week: str, current: dict, warn: Callable[[str], None]) -> list[str]:
    """The first revision of a week against the engine's draft, which it keeps."""
    from redline.analysis import weekly_draft
    from redline.model import rulebook
    from redline.model.weekly_plan import draft_change_errors
    today = dt.date.fromisoformat(current["computed_at"][:10])

    def check() -> list[str]:
        found = weekly_draft.frame(store, week, today)
        if found is None:
            return []
        draft = weekly_draft.draft(found, current["computed_at"])
        current["draft"] = draft
        floor_pct = rulebook.current().parameters.cycle_floor_pct
        floor = found.bound.volume_km * floor_pct / 100 if floor_pct is not None else None
        return draft_change_errors(draft, current, floor)

    return _guarded("draft", _where(week, current), wording.DRAFT_UNREADABLE, warn, check)


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
    current.pop("draft", None)
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
        *rpe_band_errors(previous, current, where),
        *easy_bound_errors(previous, current, where),
        *prescription.errors(previous, current, where),
        *decision_errors(current, where),
        *fuelling_errors(current, where),
        *taper_errors(store, week, current, warn),
        *pain_errors(store, week, current, existing, warn),
        *progression_errors(store, week, current, previous, warn),
        *gut_errors(store, week, current, warn),
        *cycle_errors(store, week, current, previous, warn),
        *(draft_errors(store, week, current, warn) if existing is None else []),
    ]
    if mistakes:
        raise SchemaError(mistakes)
    validate({"week": week, "revisions": [*(existing or {}).get("revisions", []), current]})
    return previous, current
