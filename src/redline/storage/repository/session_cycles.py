"""The cycle of a planned session: start, linking, status."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from redline.model import iso_week, ledger, local_time, session_kinds
from redline.model.activity import Activity
from redline.model.vocabulary import CycleState, EnrichmentState, MeasurementState

from ..database import StorageError
from ..schema import (
    observation_metrics,
    observations,
    planned_sessions,
    session_cycles,
    strava_activity_intakes,
    weekly_plans,
)
from ._common import _audit, _plain
from .observations import Observations


class SessionCycles(Observations):
    @staticmethod
    def _strava_measurements(activity: Activity,
                             intake: dict | None = None) -> dict[str, dict[str, Any]]:
        measurements: dict[str, dict[str, Any]] = {
            "rpe": {"state": MeasurementState.PENDING}, "enjoyment": {"state": MeasurementState.PENDING},
            "elevation_loss_m": {"state": MeasurementState.PENDING},
            "wbgt_c": ({"state": MeasurementState.PENDING} if activity.weather_query
                       else {"state": MeasurementState.UNAVAILABLE}),
        }
        raw = {
            "distance_km": ("km", activity.distance_km),
            "duration_s": (None, activity.moving_s or None),
            "pace_s_per_km": (None, activity.pace_s_per_km),
            "hr_avg_bpm": ("hr_avg", activity.hr_avg_bpm),
            "hr_max_bpm": ("hr_max", activity.hr_max_bpm),
            "cadence_spm": ("cadence", activity.cadence_spm or activity.cadence_rpm),
            "elevation_gain_m": ("dplus", activity.elevation_gain_m),
        }
        for metric, (field, value) in raw.items():
            if value is None:
                continue
            if field:
                canonical = ledger.canonical_value(field, str(value))
                value = float(canonical)
                if ledger.input_field(field).decimals == 0:
                    value = int(value)
            measurements[metric] = {"state": MeasurementState.KNOWN, "value": value}
        if intake:
            for prefix, metric in (("elevation_loss", "elevation_loss_m"),
                                   ("wbgt", "wbgt_c")):
                state = intake.get(f"{prefix}_state")
                if state == EnrichmentState.KNOWN:
                    measurements[metric] = {
                        "state": MeasurementState.KNOWN, "value": intake[f"{prefix}_value"]}
                elif state == EnrichmentState.UNAVAILABLE:
                    measurements[metric] = {"state": MeasurementState.UNAVAILABLE}
        return measurements

    @staticmethod
    def _observation_kind_for_workout(workout: str) -> str | None:
        kind = session_kinds.BY_SLUG.get(workout)
        return workout if kind is not None and kind.recorded else None

    @staticmethod
    def _compatible_workouts(sport: str) -> tuple[str, ...]:
        return session_kinds.planned_for_sport(sport)

    @staticmethod
    def _split_session_key(session_key: str) -> tuple[str, str]:
        try:
            week, logical_key = session_key.split(":", 1)
        except ValueError as exc:
            raise StorageError("session_key expected as 2030-W34:identifier") from exc
        if not week or not logical_key:
            raise StorageError("incomplete session_key")
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
            raise StorageError(f"unknown session: {session_key}")
        return row

    def declare_session_start_in(self, connection: Connection, payload: dict,
                                 *, actor: str) -> None:
        cycle = self._cycle_by_key_in(connection, payload["session_key"], lock=True)
        declared_at = payload["declared_at"]
        if declared_at.tzinfo is None:
            raise StorageError("declared_at must carry a time zone")
        if cycle["declared_started_at"] and cycle["declared_started_at"] != declared_at:
            raise StorageError("the declared start already exists")
        connection.execute(session_cycles.update().where(
            session_cycles.c.id == cycle["id"]).values(
                declared_started_at=declared_at,
                started_at=cycle["observed_started_at"] or declared_at,
                state=(cycle["state"] if cycle["state"] in {CycleState.COMPLETED, CycleState.CLOSED}
                       else CycleState.STARTED), updated_at=sa.func.now()))
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
            session_cycles.c.state.not_in((CycleState.CANCELLED, CycleState.CLOSED)),
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
        return f"{iso_week.of(local_day)}:{candidates[0]['logical_key']}"

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
                raise StorageError("Strava activity unknown or without a start time")
            planned = connection.execute(sa.select(planned_sessions).where(
                planned_sessions.c.id == cycle["source_session_id"])).mappings().one()
            local_day = intake["started_at"].astimezone(
                local_time.zone()).date()
            if (local_day != planned["scheduled_on"]
                    or planned["workout"] not in self._compatible_workouts(intake["sport"])):
                raise StorageError("activity incompatible with the date or the session type")
            kind = self._observation_kind_for_workout(planned["workout"])
            if kind is None:
                raise StorageError("this session type cannot receive a Strava activity")
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
            raise StorageError("Strava activity unknown or without a start time")
        linked = connection.execute(sa.select(session_cycles.c.id).where(
            session_cycles.c.observation_id == observation["id"],
            session_cycles.c.id != cycle["id"])).scalar_one_or_none()
        if linked:
            raise StorageError("this activity is already attached to another session")
        planned = connection.execute(sa.select(planned_sessions).where(
            planned_sessions.c.id == cycle["source_session_id"])).mappings().one()
        local_day = observation["started_at"].astimezone(local_time.zone()).date()
        if (local_day != planned["scheduled_on"]
                or self._workout_for_observation(observation["kind"]) != planned["workout"]):
            raise StorageError("activity incompatible with the date or the session type")
        self._link_cycle_observation_in(connection, cycle, observation, actor=actor)

    def _link_cycle_observation_in(self, connection: Connection,
                                   cycle: Any, observation: Any, *, actor: str) -> None:
        if cycle["observation_id"] and cycle["observation_id"] != observation["id"]:
            raise StorageError("this session is already attached to another observation")
        observed_at = observation["started_at"]
        planned = connection.execute(sa.select(planned_sessions).where(
            planned_sessions.c.id == cycle["source_session_id"])).mappings().one()
        if planned["carbs_target_g_h"] is not None:
            connection.execute(observation_metrics.update().where(
                observation_metrics.c.observation_id == observation["id"],
            ).values({observation_metrics.c[f"{metric}_state"]: sa.case(
                (observation_metrics.c[f"{metric}_state"] == MeasurementState.KNOWN, MeasurementState.KNOWN),
                else_=MeasurementState.PENDING) for metric in ledger.FUELLING_ASKED}))
        state = CycleState.COMPLETED
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
                                      session_cycles.c.state.not_in((CycleState.CANCELLED, CycleState.CLOSED)),
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
