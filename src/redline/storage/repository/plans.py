"""The weekly program: revisions, sessions, gates, arbitration."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from redline.model import iso_week, ledger, prescription, session_cycle, weekly_plan
from redline.model import vocabulary as voc

from ..database import StorageError
from ..plan_policy import enforce_gate_resolution
from ..schema import (
    coach_predictions,
    deadlines,
    observation_metrics,
    observations,
    plan_gates,
    plan_revisions,
    planned_session_sources,
    planned_sessions,
    proactive_jobs,
    session_cycles,
    weekly_plans,
)
from ._common import StoreBase, _audit, _request_projection, _uuid


class Plans(StoreBase):
    def append_plan_revision(self, week: str, revision: dict,
                             *, actor: str = "application") -> dict:
        with self.database.transaction() as connection:
            return self.append_plan_revision_in(connection, week, revision, actor=actor)

    def append_plan_revision_in(self, connection: Connection, week: str, revision: dict,
                                *, actor: str = "application") -> dict:
        intention_problems = weekly_plan.intention_errors(revision, f"{week}.new_revision")
        if intention_problems:
            raise StorageError("\n".join(intention_problems))
        forbidden = [session["id"] for session in revision.get("sessions", [])
                     if session.get("status") not in voc.INTENT_STATUSES]
        if forbidden:
            raise StorageError(
                ("a revision only carries the planned/cancelled intent; execution belongs to the"
                 " cycles: ") + ", ".join(forbidden))
        previous_revision = connection.execute(sa.select(
            plan_revisions.c.id, plan_revisions.c.computed_at,
        ).join(weekly_plans, weekly_plans.c.id == plan_revisions.c.plan_id).where(
            weekly_plans.c.athlete_id == self.athlete_id,
            weekly_plans.c.iso_week == week,
        ).order_by(plan_revisions.c.revision.desc()).limit(1)).mappings().one_or_none()
        if previous_revision is not None:
            enforce_gate_resolution(
                connection, self.athlete_id, previous_revision, revision)
            previous_sessions = {
                row["logical_key"]: row for row in connection.execute(
                    sa.select(planned_sessions).where(
                        planned_sessions.c.revision_id == previous_revision["id"])
                ).mappings()
            }
            rises = weekly_plan.session_rises(previous_sessions, revision.get("sessions", []))
        else:
            week_before = self._load_plan_connection(connection, iso_week.shift(week, -1))
            rises = weekly_plan.week_rises(
                week_before["revisions"][-1] if week_before else None, revision)
        if rises:
            self._consume_challenged_increase(connection, rises)
        unavailable = list(connection.execute(sa.select(deadlines).where(
            deadlines.c.athlete_id == self.athlete_id,
            deadlines.c.kind == "availability",
            deadlines.c.status != voc.DeadlineStatus.DONE)).mappings())
        for session in revision.get("sessions", []):
            if session.get("status") == voc.SessionStatus.CANCELLED or not session.get("demanding"):
                continue
            day = session["date"]
            if isinstance(day, str):
                day = dt.date.fromisoformat(day)
            for event in unavailable:
                start = event["starts_on"] or event["due_on"]
                end = event["ends_on"] or event["due_on"]
                if start <= day <= end:
                    raise StorageError(
                        f"demanding session {session["id"]} on {day} during the typed unavailability {event["deadline_key"]}")
        plan_id = connection.execute(pg_insert(weekly_plans).values(
            id=uuid.uuid4(), athlete_id=self.athlete_id, iso_week=week,
        ).on_conflict_do_update(
            constraint="uq_weekly_plans_week", set_={"iso_week": week},
        ).returning(weekly_plans.c.id)).scalar_one()
        revision_id = _uuid(revision.get("id"))
        connection.execute(plan_revisions.insert().values(
            id=revision_id, plan_id=plan_id, revision=revision["revision"],
            computed_at=revision["computed_at"], reason=revision["reason"],
            evidence=revision.get("evidence", []),
            constraints=revision.get("constraints", []),
            changes=revision.get("changes", []),
            volume_target_km=revision.get("volume_target_km"),
            decision=revision.get("decision"),
            state_snapshot=revision.get("state_snapshot"),
        ))
        cycle_inputs = []
        for session in revision.get("sessions", []):
            session_id = _uuid(session.get("db_id"))
            connection.execute(planned_sessions.insert().values(
                id=session_id, revision_id=revision_id, logical_key=session["id"],
                title=session["title"], scheduled_on=session["date"],
                start_time=(dt.time.fromisoformat(session["start_time"])
                            if session.get("start_time") else None), kind=session["kind"],
                workout=session["workout"], routine=session.get("routine"),
                status=session["status"],
                distance_target_km=session.get("distance_target_km"),
                distance_cap_km=session.get("distance_cap_km"),
                duration_cap_min=session.get("duration_cap_min"),
                rpe_min=session.get("rpe_min"), rpe_max=session.get("rpe_max"),
                hr_zone=session.get("hr_zone"), hr_cap_bpm=session.get("hr_cap_bpm"),
                cadence_target=session.get("cadence_target"),
                **{name: session.get(name) for name in prescription.FIELDS},
                demanding=session["demanding"],
                priority=session.get("priority"),
                transition=session.get("transition"),
                location_label=session.get("location_label"),
                carbs_target_g_h=session.get("carbs_target_g_h"),
                carbs_mix=session.get("carbs_mix"),
                strength_spec=session.get("strength"),
                note=session.get("note"),
            ))
            for position, source in enumerate(session.get("sources", []), 1):
                connection.execute(planned_session_sources.insert().values(
                    session_id=session_id, position=position, source=source))
            cycle_inputs.append((session_id, session))
        for gate in revision.get("gates", []):
            connection.execute(plan_gates.insert().values(
                id=uuid.uuid4(), revision_id=revision_id, logical_key=gate["id"],
                due_on=gate["due"], question=gate["question"], on_fail=gate["on_fail"],
                # `when` is a reserved SQL word: the column is `applies_when`, and the
                # translation lives HERE, the only module that knows both vocabularies.
                # The model must not carry a database column name.
                applies_when=gate["when"],
                holds=gate["holds"] if "holds" in gate else sa.null()))
        self._sync_session_cycles(connection, plan_id, cycle_inputs)
        _audit(connection, actor, "plan.revised", "plan_revision", revision_id,
               {"week": week, "revision": revision["revision"]})
        _request_projection(connection)
        loaded = self._load_plan_connection(connection, week)
        if loaded is None:  # impossible after the INSERT, barring transactional corruption
            raise StorageError(f"plan {week} not found after writing")
        return loaded

    def active_plans(self) -> dict[str, dict]:
        """The active revision of each week that has a program, keyed by ISO week."""
        with self.database.engine.connect() as connection:
            weeks = connection.execute(sa.select(weekly_plans.c.iso_week).where(
                weekly_plans.c.athlete_id == self.athlete_id
            ).order_by(weekly_plans.c.iso_week)).scalars().all()
            plans = {week: self._load_plan_connection(connection, week) for week in weeks}
        return {week: plan["revisions"][-1] for week, plan in plans.items() if plan}

    def fuelled_sessions(self) -> list[dict]:
        """Each fuelled session run, newest first: its day, the target it was prescribed,
        the carbohydrate taken and the gut symptoms, None when not known."""
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(
                observations.c.occurred_on, planned_sessions.c.logical_key,
                planned_sessions.c.carbs_target_g_h,
                observation_metrics.c.carbs_g_h, observation_metrics.c.carbs_g_h_state,
                observation_metrics.c.gut_symptoms, observation_metrics.c.gut_symptoms_state,
            ).join(session_cycles,
                   session_cycles.c.source_session_id == planned_sessions.c.id).join(
                observations, observations.c.id == session_cycles.c.observation_id).join(
                observation_metrics,
                observation_metrics.c.observation_id == session_cycles.c.observation_id).where(
                    session_cycles.c.athlete_id == self.athlete_id,
                    planned_sessions.c.carbs_target_g_h.is_not(None),
            ).order_by(session_cycles.c.observed_started_at.desc())).mappings().all()

        def known(row: Any, metric: str) -> float | None:
            return float(row[metric]) if row[f"{metric}_state"] == "known" else None

        return [{"on": row["occurred_on"], "session": row["logical_key"],
                 "target_g_h": float(row["carbs_target_g_h"]),
                 "carbs_taken_g_h": known(row, "carbs_g_h"),
                 "gut_symptoms": known(row, "gut_symptoms")} for row in rows]

    def _consume_challenged_increase(self, connection: Connection, rises: list[str]) -> None:
        """A load increase uses one open, challenged and unused increase prediction."""
        prediction = connection.execute(sa.select(coach_predictions).where(
            coach_predictions.c.athlete_id == self.athlete_id,
            coach_predictions.c.direction == "increase",
            coach_predictions.c.status == "open",
            coach_predictions.c.challenged_at.is_not(None),
            coach_predictions.c.used_at.is_(None),
        ).order_by(coach_predictions.c.challenged_at.desc()).limit(1).with_for_update(
        )).mappings().one_or_none()
        if prediction is None:
            raise StorageError(
                f"load increase blocked ({'; '.join(rises)}): create a prediction "
                "direction=increase, then let the challenger challenge it before revising")
        connection.execute(coach_predictions.update().where(
            coach_predictions.c.id == prediction["id"]
        ).values(used_at=dt.datetime.now(dt.UTC)))

    def _sync_session_cycles(self, connection: Connection, plan_id: uuid.UUID,
                             sessions: list[tuple[uuid.UUID, dict]]) -> None:
        """Project the active revision onto one stable cycle per logical session.

        An append-only revision recreates the ``planned_sessions`` rows. The cycle
        survives revisions: its identity is ``plan_id + logical_key``.
        """
        existing = {row["logical_key"]: row for row in connection.execute(
            sa.select(session_cycles).where(session_cycles.c.plan_id == plan_id)
        ).mappings()}
        present = set()
        now = dt.datetime.now(dt.UTC)
        for source_session_id, session in sessions:
            logical_key = session["id"]
            present.add(logical_key)
            digest = session_cycle.plan_digest(session)
            planned_for = session_cycle.scheduled_for(
                session["date"], session.get("start_time"))
            previous = existing.get(logical_key)
            changed = previous is not None and previous["plan_digest"] != digest
            state = session_cycle.synchronized_state(
                previous["state"] if previous else None, session["status"], changed=changed)
            if previous is None:
                connection.execute(session_cycles.insert().values(
                    id=uuid.uuid4(), athlete_id=self.athlete_id, plan_id=plan_id,
                    logical_key=logical_key, source_session_id=source_session_id,
                    state=state, planned_for=planned_for, plan_digest=digest,
                    completed_at=now if state == voc.CycleState.COMPLETED else None,
                    updated_at=now))
                continue
            if state == voc.CycleState.CANCELLED:
                self._cancel_gut_review(connection, previous["id"])
            connection.execute(session_cycles.update().where(
                session_cycles.c.id == previous["id"]
            ).values(
                source_session_id=source_session_id, state=state,
                planned_for=planned_for, plan_digest=digest,
                completed_at=(now if state == voc.CycleState.COMPLETED and previous["completed_at"] is None
                              else previous["completed_at"]),
                updated_at=now))

        for logical_key, previous in existing.items():
            if logical_key in present or previous["state"] in session_cycle.TERMINAL_STATES:
                continue
            self._cancel_gut_review(connection, previous["id"])
            connection.execute(session_cycles.update().where(
                session_cycles.c.id == previous["id"]
            ).values(state=voc.CycleState.CANCELLED, updated_at=now))

    @staticmethod
    def _cancel_gut_review(connection: Connection, cycle_id: uuid.UUID) -> None:
        """A session cancelled or removed from the plan no longer needs a gut-feedback request."""
        connection.execute(proactive_jobs.update().where(
            proactive_jobs.c.job_key == f"gut-review:{cycle_id}",
            proactive_jobs.c.status == voc.JobStatus.PENDING,
        ).values(status=voc.JobStatus.DONE, completed_at=sa.func.now()))

    def load_plan(self, week: str) -> dict | None:
        with self.database.engine.connect() as connection:
            return self._load_plan_connection(connection, week)

    def _load_plan_connection(self, connection: Connection, week: str) -> dict | None:
        plan = connection.execute(sa.select(weekly_plans).where(
            weekly_plans.c.athlete_id == self.athlete_id,
            weekly_plans.c.iso_week == week)).mappings().one_or_none()
        if plan is None:
            return None
        revision_rows = connection.execute(sa.select(plan_revisions).where(
            plan_revisions.c.plan_id == plan["id"]
        ).order_by(plan_revisions.c.revision)).mappings().all()
        revisions = []
        for row in revision_rows:
            revision = {
                "revision": row["revision"], "computed_at": row["computed_at"].isoformat(),
                "reason": row["reason"], "evidence": row["evidence"],
                "constraints": row["constraints"],
                "changes": row["changes"],
                "sessions": [], "gates": [],
            }
            if row["volume_target_km"] is not None:
                revision["volume_target_km"] = float(row["volume_target_km"])
            for name in ("decision", "state_snapshot"):
                if row[name] is not None:
                    revision[name] = row[name]
            session_rows = connection.execute(sa.select(planned_sessions).where(
                planned_sessions.c.revision_id == row["id"]
            ).order_by(planned_sessions.c.scheduled_on,
                       planned_sessions.c.logical_key)).mappings().all()
            cycle_states = {item.logical_key: item.state for item in connection.execute(
                sa.select(session_cycles.c.logical_key, session_cycles.c.state).where(
                    session_cycles.c.plan_id == plan["id"])
            )}
            for stored in session_rows:
                visible_status = stored["status"]
                if cycle_states.get(stored["logical_key"]) in {voc.CycleState.COMPLETED, voc.CycleState.CLOSED}:
                    visible_status = voc.SessionStatus.DONE
                session = {
                    "id": stored["logical_key"], "title": stored["title"],
                    "date": stored["scheduled_on"].isoformat(), "kind": stored["kind"],
                    "workout": stored["workout"], "status": visible_status,
                    "demanding": stored["demanding"],
                }
                if stored["start_time"] is not None:
                    session["start_time"] = stored["start_time"].strftime("%H:%M")
                for key in ("distance_target_km", "distance_cap_km"):
                    if stored[key] is not None:
                        session[key] = float(stored[key])
                for key in ("routine", "duration_cap_min", "rpe_min", "rpe_max", "hr_zone",
                            "hr_cap_bpm", "cadence_target", "priority", "transition",
                            "location_label", "carbs_target_g_h", "carbs_mix", "note",
                            *prescription.FIELDS):
                    if stored[key] is not None:
                        session[key] = stored[key]
                if stored["strength_spec"] is not None:
                    session["strength"] = dict(stored["strength_spec"])
                session["sources"] = list(connection.execute(sa.select(
                    planned_session_sources.c.source).where(
                        planned_session_sources.c.session_id == stored["id"]
                    ).order_by(planned_session_sources.c.position)).scalars())
                revision["sessions"].append(session)
            for gate in connection.execute(sa.select(plan_gates).where(
                    plan_gates.c.revision_id == row["id"])).mappings():
                stored_gate = {
                    "id": gate["logical_key"], "due": gate["due_on"].isoformat(),
                    "question": gate["question"], "on_fail": gate["on_fail"]}
                stored_gate["when"] = gate["applies_when"]
                if gate["holds"] is not None:
                    stored_gate["holds"] = list(gate["holds"])
                revision["gates"].append(stored_gate)
            revisions.append(revision)
        return {"week": week, "revisions": revisions}

    def plan_needs_arbitration(self, week: str, revision: int) -> bool:
        """Did an unlinked load arrive after this revision?

        The watermark comes from PostgreSQL, not from a `computed_at` supplied in the
        JSON. An activity already known when the revision was made was therefore
        arbitrated; a planned activity linked to its cycle is never a surprise. This
        reading stays true when the input format evolves, unlike a comparison of
        hashes of different contracts.
        """
        with self.database.engine.connect() as connection:
            persisted_at = connection.execute(sa.select(
                plan_revisions.c.persisted_at,
            ).join(weekly_plans, weekly_plans.c.id == plan_revisions.c.plan_id).where(
                weekly_plans.c.athlete_id == self.athlete_id,
                weekly_plans.c.iso_week == week,
                plan_revisions.c.revision == revision,
            )).scalar_one_or_none()
            if persisted_at is None:
                raise StorageError(
                    f"revision {revision} of plan {week} not found")
            monday, sunday = iso_week.monday(week), iso_week.sunday(week)
            unlinked = sa.select(observations.c.id).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.occurred_on.between(monday, sunday),
                observations.c.kind.in_(ledger.EFFORT_TYPES),
                observations.c.created_at > persisted_at,
                ~sa.exists(sa.select(session_cycles.c.id).where(
                    session_cycles.c.observation_id == observations.c.id)),
            )
            return bool(connection.execute(sa.select(sa.exists(unlinked))).scalar_one())
