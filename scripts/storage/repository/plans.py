"""Le programme hebdomadaire : révisions, séances, contrôles, arbitrage."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from scripts.model import ledger, session_cycle, weekly_plan

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
        intention_problems = weekly_plan.intention_errors(revision, f"{week}.nouvelle_revision")
        if intention_problems:
            raise StorageError("\n".join(intention_problems))
        forbidden = [session["id"] for session in revision.get("sessions", [])
                     if session.get("status") not in weekly_plan.INTENT_STATUSES]
        if forbidden:
            raise StorageError(
                "une révision ne porte que l'intention planned/cancelled ; "
                "l'exécution appartient aux cycles : " + ", ".join(forbidden))
        self._enforce_gut_progression(connection, revision.get("sessions", []))
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
            if self._raises_load(previous_sessions, revision.get("sessions", [])):
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
                        "hausse de charge bloquée : crée une prédiction direction=increase, "
                        "puis laisse le contradicteur la challenger avant de réviser")
                connection.execute(coach_predictions.update().where(
                    coach_predictions.c.id == prediction["id"]
                ).values(used_at=dt.datetime.now(dt.UTC)))
        unavailable = list(connection.execute(sa.select(deadlines).where(
            deadlines.c.athlete_id == self.athlete_id,
            deadlines.c.kind == "availability",
            deadlines.c.status != "done")).mappings())
        for session in revision.get("sessions", []):
            if session.get("status") == "cancelled" or not session.get("demanding"):
                continue
            day = session["date"]
            if isinstance(day, str):
                day = dt.date.fromisoformat(day)
            for event in unavailable:
                start = event["starts_on"] or event["due_on"]
                end = event["ends_on"] or event["due_on"]
                if start <= day <= end:
                    raise StorageError(
                        f"séance exigeante {session['id']} le {day} pendant "
                        f"l'indisponibilité typée {event['deadline_key']}")
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
                demanding=session["demanding"],
                transition=session.get("transition"),
                location_label=session.get("location_label"),
                carbs_target_g_h=session.get("carbs_target_g_h"),
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
                # `when` est un mot réservé SQL : la colonne s'appelle `applies_when`,
                # et la traduction vit ICI, dans le seul module qui connaît les deux
                # vocabulaires. Le modèle n'a pas à porter un nom de base de données.
                applies_when=gate["when"],
                holds=gate["holds"] if "holds" in gate else sa.null()))
        self._sync_session_cycles(connection, plan_id, cycle_inputs)
        _audit(connection, actor, "plan.revised", "plan_revision", revision_id,
               {"week": week, "revision": revision["revision"]})
        _request_projection(connection)
        loaded = self._load_plan_connection(connection, week)
        if loaded is None:  # impossible après l'INSERT, sauf corruption transactionnelle
            raise StorageError(f"programme {week} introuvable après écriture")
        return loaded

    def active_plans(self) -> dict[str, dict]:
        """La révision active de chaque semaine qui a un programme, par semaine ISO."""
        with self.database.engine.connect() as connection:
            weeks = connection.execute(sa.select(weekly_plans.c.iso_week).where(
                weekly_plans.c.athlete_id == self.athlete_id
            ).order_by(weekly_plans.c.iso_week)).scalars().all()
            plans = {week: self._load_plan_connection(connection, week) for week in weeks}
        return {week: plan["revisions"][-1] for week, plan in plans.items() if plan}

    def _enforce_gut_progression(self, connection: Connection, proposed: list[dict]) -> None:
        targets = [session["carbs_target_g_h"] for session in proposed
                   if session.get("status") != "cancelled"
                   and session.get("carbs_target_g_h") is not None]
        if not targets:
            return
        last = connection.execute(sa.select(
            planned_sessions.c.carbs_target_g_h,
            observation_metrics.c.gut_symptoms,
        ).join(session_cycles,
               session_cycles.c.source_session_id == planned_sessions.c.id).join(
            observation_metrics,
            observation_metrics.c.observation_id == session_cycles.c.observation_id).where(
                session_cycles.c.athlete_id == self.athlete_id,
                planned_sessions.c.carbs_target_g_h.is_not(None),
                observation_metrics.c.carbs_g_h_state == "known",
                observation_metrics.c.gut_symptoms_state == "known",
        ).order_by(session_cycles.c.observed_started_at.desc()).limit(1)).mappings().one_or_none()
        if last is None:
            return
        baseline = int(last["carbs_target_g_h"])
        highest = max(targets)
        if highest <= baseline:
            return
        if float(last["gut_symptoms"]) > 3:
            raise StorageError(
                "progression digestive bloquée : la dernière exposition dépasse 3/10")
        if highest > baseline + 15:
            raise StorageError(
                f"progression digestive bloquée : {highest} g/h dépasse le palier "
                f"autorisé de {baseline + 15} g/h")

    @staticmethod
    def _raises_load(previous: dict[str, Any], proposed: list[dict]) -> bool:
        """Détecte une hausse concrète, sans confondre clôture et charge nouvelle."""
        numeric = ("distance_target_km", "distance_cap_km", "duration_cap_min",
                   "carbs_target_g_h")
        for session in proposed:
            if session.get("status") == "cancelled":
                continue
            old = previous.get(session["id"])
            if old is None:
                if session.get("demanding"):
                    return True
                continue
            if session.get("demanding") and not old["demanding"]:
                return True
            for field in numeric:
                new_value, old_value = session.get(field), old[field]
                if new_value is not None and (old_value is None or float(new_value) > float(old_value)):
                    return True
        return False

    def _sync_session_cycles(self, connection: Connection, plan_id: uuid.UUID,
                             sessions: list[tuple[uuid.UUID, dict]]) -> None:
        """Projette la révision active vers un cycle stable par séance logique.

        Une révision append-only recrée les lignes ``planned_sessions``. Le cycle, lui,
        survit aux révisions : son identité est ``plan_id + logical_key``.
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
                    completed_at=now if state == "completed" else None,
                    updated_at=now))
                continue
            if state == "cancelled":
                self._cancel_gut_review(connection, previous["id"])
            connection.execute(session_cycles.update().where(
                session_cycles.c.id == previous["id"]
            ).values(
                source_session_id=source_session_id, state=state,
                planned_for=planned_for, plan_digest=digest,
                completed_at=(now if state == "completed" and previous["completed_at"] is None
                              else previous["completed_at"]),
                updated_at=now))

        for logical_key, previous in existing.items():
            if logical_key in present or previous["state"] in session_cycle.TERMINAL_STATES:
                continue
            self._cancel_gut_review(connection, previous["id"])
            connection.execute(session_cycles.update().where(
                session_cycles.c.id == previous["id"]
            ).values(state="cancelled", updated_at=now))

    @staticmethod
    def _cancel_gut_review(connection: Connection, cycle_id: uuid.UUID) -> None:
        """Une séance annulée ou retirée du plan n'a plus de retour digestif à demander."""
        connection.execute(proactive_jobs.update().where(
            proactive_jobs.c.job_key == f"gut-review:{cycle_id}",
            proactive_jobs.c.status == "pending",
        ).values(status="done", completed_at=sa.func.now()))

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
                if cycle_states.get(stored["logical_key"]) in {"completed", "closed"}:
                    visible_status = "done"
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
                            "hr_cap_bpm", "cadence_target", "transition", "location_label",
                            "carbs_target_g_h", "note"):
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
        """Une charge non rattachée est-elle entrée après cette révision ?

        Le watermark vient de PostgreSQL, pas de `computed_at` fourni dans le JSON.
        Une activité déjà connue au moment de la révision a donc été arbitrée ; une
        activité planifiée liée à son cycle n'est jamais une surprise. Cette lecture
        reste vraie lorsque le format des entrées évolue, contrairement à une
        comparaison de hashes de contrats différents.
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
                    f"révision {revision} du programme {week} introuvable")
            year, number = week.split("-W")
            monday = dt.date.fromisocalendar(int(year), int(number), 1)
            sunday = monday + dt.timedelta(days=6)
            unlinked = sa.select(observations.c.id).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.occurred_on.between(monday, sunday),
                observations.c.kind.in_(ledger.EFFORT_TYPES),
                observations.c.created_at > persisted_at,
                ~sa.exists(sa.select(session_cycles.c.id).where(
                    session_cycles.c.observation_id == observations.c.id)),
            )
            return bool(connection.execute(sa.select(sa.exists(unlinked))).scalar_one())
