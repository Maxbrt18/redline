"""The cycle of a planned session: start, linking, status."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from redline.model import local_time, session_kinds
from redline.model.activity import Activity

from ..database import StorageError
from ..schema import (
    observation_metrics,
    observations,
    planned_sessions,
    session_cycles,
    strava_activity_intakes,
    weekly_plans,
)
from ._common import StoreBase, _audit, _plain


class SessionCycles(StoreBase):
    @staticmethod
    def _split_session_key(session_key: str) -> tuple[str, str]:
        try:
            week, logical_key = session_key.split(":", 1)
        except ValueError as exc:
            raise StorageError("session_key attendu sous la forme 2026-W34:identifiant") from exc
        if not week or not logical_key:
            raise StorageError("session_key incomplet")
        return week, logical_key

    def _cycle_by_key_in(self, connection: Connection, session_key: str,
                         *, lock: bool = False):
        week, logical_key = self._split_session_key(session_key)
        query = sa.select(session_cycles).join(
            weekly_plans, weekly_plans.c.id == session_cycles.c.plan_id
        ).where(
            session_cycles.c.athlete_id == self.athlete_id,
            weekly_plans.c.iso_week == week,
            session_cycles.c.logical_key == logical_key,
        )
        if lock:
            query = query.with_for_update(of=session_cycles)
        row = connection.execute(query).mappings().one_or_none()
        if row is None:
            raise StorageError(f"séance inconnue : {session_key}")
        return row

    def declare_session_start(self, payload: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.declare_session_start_in(connection, payload, actor=actor)

    def declare_session_start_in(self, connection: Connection, payload: dict,
                                 *, actor: str) -> None:
        cycle = self._cycle_by_key_in(connection, payload["session_key"], lock=True)
        declared_at = payload["declared_at"]
        if declared_at.tzinfo is None:
            raise StorageError("declared_at doit porter un fuseau horaire")
        if cycle["declared_started_at"] and cycle["declared_started_at"] != declared_at:
            raise StorageError("le départ déclaré existe déjà")
        connection.execute(session_cycles.update().where(
            session_cycles.c.id == cycle["id"]).values(
                declared_started_at=declared_at,
                started_at=cycle["observed_started_at"] or declared_at,
                state=(cycle["state"] if cycle["state"] in {"completed", "closed"}
                       else "started"), updated_at=sa.func.now()))
        _audit(connection, actor, "session.started.declared", "session_cycle", cycle["id"],
               {"declared_at": declared_at.isoformat()})

    @staticmethod
    def _workout_for_observation(kind: str) -> str | None:
        found = session_kinds.BY_SLUG.get(kind)
        return kind if found is not None and found.planned else None

    def _auto_match_observation_in(self, connection: Connection, observation_id: uuid.UUID,
                                   session_key: str | None, *, actor: str) -> str | None:
        observation = connection.execute(sa.select(observations).where(
            observations.c.id == observation_id).with_for_update()).mappings().one()
        workout = self._workout_for_observation(observation["kind"])
        if workout is None:
            return None
        local_day = observation["started_at"].astimezone(
            local_time.zone()).date()
        query = sa.select(session_cycles).join(
            planned_sessions, planned_sessions.c.id == session_cycles.c.source_session_id
        ).join(weekly_plans, weekly_plans.c.id == session_cycles.c.plan_id).where(
            session_cycles.c.athlete_id == self.athlete_id,
            session_cycles.c.observation_id.is_(None),
            session_cycles.c.state.not_in(("cancelled", "closed")),
            planned_sessions.c.scheduled_on == local_day,
            planned_sessions.c.workout == workout,
        )
        if observation.get("routine"):
            query = query.where(planned_sessions.c.routine == observation["routine"])
        if session_key:
            week, logical_key = self._split_session_key(session_key)
            query = query.where(weekly_plans.c.iso_week == week,
                                session_cycles.c.logical_key == logical_key)
        candidates = connection.execute(query.with_for_update(
            of=session_cycles)).mappings().all()
        if len(candidates) != 1:
            return None
        self._link_cycle_observation_in(connection, candidates[0], observation, actor=actor)
        return f"{local_day.isocalendar().year}-W{local_day.isocalendar().week:02d}:" \
               f"{candidates[0]['logical_key']}"

    def match_session(self, session_key: str, strava_activity_id: int,
                      *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.match_session_in(connection, session_key, strava_activity_id, actor=actor)

    def match_session_in(self, connection: Connection, session_key: str,
                         strava_activity_id: int, *, actor: str) -> None:
        cycle = self._cycle_by_key_in(connection, session_key, lock=True)
        observation = connection.execute(sa.select(observations).where(
            observations.c.athlete_id == self.athlete_id,
            observations.c.strava_activity_id == strava_activity_id,
        ).with_for_update()).mappings().one_or_none()
        if observation is None:
            intake = connection.execute(sa.select(strava_activity_intakes).where(
                strava_activity_intakes.c.activity_id == strava_activity_id,
                strava_activity_intakes.c.athlete_id == self.athlete_id,
            ).with_for_update()).mappings().one_or_none()
            if intake is None or intake["started_at"] is None:
                raise StorageError("activité Strava inconnue ou sans heure de départ")
            planned = connection.execute(sa.select(planned_sessions).where(
                planned_sessions.c.id == cycle["source_session_id"])).mappings().one()
            local_day = intake["started_at"].astimezone(
                local_time.zone()).date()
            if (local_day != planned["scheduled_on"]
                    or planned["workout"] not in self._compatible_workouts(intake["sport"])):
                raise StorageError("activité incompatible avec la date ou le type de séance")
            kind = self._observation_kind_for_workout(planned["workout"])
            if kind is None:
                raise StorageError("ce type de séance ne peut pas recevoir une activité Strava")
            activity = Activity.from_json(intake["distilled"])
            self.add_observation_in(connection, {
                "occurred_on": local_day, "started_at": intake["started_at"],
                "kind": kind, "idempotency_key": f"strava:{strava_activity_id}",
                "strava_activity_id": strava_activity_id,
                "note": f"Strava : {(activity.name or intake['sport']).strip()}",
                "source": actor,
                "measurements": self._strava_measurements(activity, dict(intake)),
                "session_key": session_key,
            }, actor=actor)
            return
        if observation["started_at"] is None:
            raise StorageError("activité Strava inconnue ou sans heure de départ")
        linked = connection.execute(sa.select(session_cycles.c.id).where(
            session_cycles.c.observation_id == observation["id"],
            session_cycles.c.id != cycle["id"])).scalar_one_or_none()
        if linked:
            raise StorageError("cette activité est déjà rattachée à une autre séance")
        planned = connection.execute(sa.select(planned_sessions).where(
            planned_sessions.c.id == cycle["source_session_id"])).mappings().one()
        local_day = observation["started_at"].astimezone(local_time.zone()).date()
        if (local_day != planned["scheduled_on"]
                or self._workout_for_observation(observation["kind"]) != planned["workout"]):
            raise StorageError("activité incompatible avec la date ou le type de séance")
        self._link_cycle_observation_in(connection, cycle, observation, actor=actor)

    def _link_cycle_observation_in(self, connection: Connection,
                                   cycle: Any, observation: Any, *, actor: str) -> None:
        if cycle["observation_id"] and cycle["observation_id"] != observation["id"]:
            raise StorageError("cette séance est déjà rattachée à une autre observation")
        observed_at = observation["started_at"]
        planned = connection.execute(sa.select(planned_sessions).where(
            planned_sessions.c.id == cycle["source_session_id"])).mappings().one()
        if planned["carbs_target_g_h"] is not None:
            connection.execute(observation_metrics.update().where(
                observation_metrics.c.observation_id == observation["id"],
            ).values(
                carbs_g_h_state=sa.case(
                    (observation_metrics.c.carbs_g_h_state == "known", "known"),
                    else_="pending"),
                gut_symptoms_state=sa.case(
                    (observation_metrics.c.gut_symptoms_state == "known", "known"),
                    else_="pending"),
            ))
        state = "completed"
        connection.execute(session_cycles.update().where(
            session_cycles.c.id == cycle["id"]).values(
                observation_id=observation["id"], observed_started_at=observed_at,
                started_at=observed_at, state=state, completed_at=sa.func.now(),
                updated_at=sa.func.now()))
        _audit(connection, actor, "session.completed.observed", "session_cycle", cycle["id"],
               {"observation_id": str(observation["id"]),
                "observed_started_at": observed_at.isoformat()})

    def week_session_links(self, week: str) -> dict[str, dict]:
        """The cycle of each session in a week and the observation that settled it.

        Read by the notebook: it sets the fact (the observation) next to the intent (the
        session). Nothing is copied; the observation is re-read by its id in
        `observation_rows`.
        """
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(
                session_cycles.c.logical_key, session_cycles.c.state,
                session_cycles.c.observation_id, session_cycles.c.observed_started_at,
            ).join(weekly_plans, weekly_plans.c.id == session_cycles.c.plan_id).where(
                session_cycles.c.athlete_id == self.athlete_id,
                weekly_plans.c.iso_week == week)).mappings().all()
        return {row["logical_key"]: dict(row) for row in rows}

    def session_cycle_status(self, week: str | None = None) -> list[dict]:
        """Diagnostic view for the coach: pending expectations and ambiguities."""
        with self.database.engine.connect() as connection:
            query = sa.select(
                weekly_plans.c.iso_week, session_cycles, planned_sessions.c.title,
                planned_sessions.c.workout, planned_sessions.c.scheduled_on,
            ).join(session_cycles, session_cycles.c.plan_id == weekly_plans.c.id).join(
                planned_sessions, planned_sessions.c.id == session_cycles.c.source_session_id
            ).where(session_cycles.c.athlete_id == self.athlete_id)
            if week:
                query = query.where(weekly_plans.c.iso_week == week)
            rows = connection.execute(query.order_by(
                planned_sessions.c.scheduled_on, session_cycles.c.logical_key)).mappings()
            return [{key: _plain(value) for key, value in dict(row).items()} for row in rows]

    def unmatched_session_activities(self) -> list[dict]:
        """Activités non liées et choix compatibles, sans décision silencieuse."""
        with self.database.engine.connect() as connection:
            observations_rows = connection.execute(sa.select(observations).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.strava_activity_id.is_not(None),
                observations.c.started_at.is_not(None),
                ~sa.exists().where(session_cycles.c.observation_id == observations.c.id),
            ).order_by(observations.c.started_at.desc()).limit(20)).mappings().all()
            result = []
            for observation in observations_rows:
                workout = self._workout_for_observation(observation["kind"])
                local_day = observation["started_at"].astimezone(
                    local_time.zone()).date()
                candidates = []
                if workout:
                    candidates = [f"{row.iso_week}:{row.logical_key}" for row in
                                  connection.execute(sa.select(
                                      weekly_plans.c.iso_week,
                                      session_cycles.c.logical_key,
                                  ).join(session_cycles,
                                         session_cycles.c.plan_id == weekly_plans.c.id).join(
                                      planned_sessions,
                                      planned_sessions.c.id == session_cycles.c.source_session_id
                                  ).where(
                                      session_cycles.c.athlete_id == self.athlete_id,
                                      session_cycles.c.observation_id.is_(None),
                                      session_cycles.c.state.not_in(("cancelled", "closed")),
                                      planned_sessions.c.scheduled_on == local_day,
                                      planned_sessions.c.workout == workout,
                                  ).order_by(weekly_plans.c.iso_week,
                                             session_cycles.c.logical_key)).all()]
                if candidates:
                    result.append({
                        "strava_activity_id": observation["strava_activity_id"],
                        "started_at": observation["started_at"].isoformat(),
                        "kind": observation["kind"], "candidates": candidates,
                    })
            return result
