"""Strava activities: captured, enriched and linked."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from redline.model import ledger, session_cycle, session_kinds
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
from ._common import _INTAKE_CORE, StoreBase, _audit, _plain, _uuid


class StravaIntake(StoreBase):
    @staticmethod
    def _observation_kind_for_workout(workout: str) -> str | None:
        kind = session_kinds.BY_SLUG.get(workout)
        return workout if kind is not None and kind.recorded else None

    @staticmethod
    def _compatible_workouts(sport: str) -> tuple[str, ...]:
        return session_kinds.planned_for_sport(sport)

    @staticmethod
    def _strava_measurements(activity: Activity,
                             intake: dict | None = None) -> dict[str, dict[str, Any]]:
        measurements: dict[str, dict[str, Any]] = {
            "rpe": {"state": "pending"}, "enjoyment": {"state": "pending"},
            "elevation_loss_m": {"state": "pending"},
            "wbgt_c": ({"state": "pending"} if activity.weather_query
                       else {"state": "unavailable"}),
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
            measurements[metric] = {"state": "known", "value": value}
        if intake:
            for prefix, metric in (("elevation_loss", "elevation_loss_m"),
                                   ("wbgt", "wbgt_c")):
                state = intake.get(f"{prefix}_state")
                if state == "known":
                    measurements[metric] = {
                        "state": "known", "value": intake[f"{prefix}_value"]}
                elif state == "unavailable":
                    measurements[metric] = {"state": "unavailable"}
        return measurements

    def ingest_strava_activity(self, activity: Activity,
                               *, actor: str = "strava-ingestion") -> dict:
        """Capture the objective facts before any coaching reasoning.

        An activity becomes a typed observation only if the plan offers a single
        candidate that day. Otherwise the capture stays durable and the ambiguity is
        exposed; no sport taxonomy is inferred from a title.
        """
        activity_id = int(activity.id or 0)
        if activity_id <= 0:
            raise StorageError("identifiant Strava invalide")
        sport = activity.sport.strip()
        if not sport:
            raise StorageError("sport Strava absent")
        started_at = (session_cycle.local_wall_clock(activity.start_local)
                      if activity.start_local else None)
        with self.database.transaction() as connection:
            existing_observation = connection.execute(sa.select(observations).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.strava_activity_id == activity_id,
            ).with_for_update()).mappings().one_or_none()
            candidates: list[dict] = []
            if started_at is not None:
                workouts = self._compatible_workouts(sport)
                if workouts:
                    rows = connection.execute(sa.select(
                        session_cycles,
                        weekly_plans.c.iso_week, planned_sessions.c.workout,
                    ).join(weekly_plans,
                           weekly_plans.c.id == session_cycles.c.plan_id).join(
                        planned_sessions,
                        planned_sessions.c.id == session_cycles.c.source_session_id).where(
                        session_cycles.c.athlete_id == self.athlete_id,
                        session_cycles.c.observation_id.is_(None),
                        session_cycles.c.state.not_in(("cancelled", "closed")),
                        planned_sessions.c.scheduled_on == started_at.date(),
                        planned_sessions.c.workout.in_(workouts),
                    ).order_by(session_cycles.c.logical_key).with_for_update(
                        of=session_cycles)).mappings().all()
                    candidates = [dict(row) for row in rows]
                    if existing_observation is not None:
                        stored_workout = self._workout_for_observation(
                            existing_observation["kind"])
                        candidates = [row for row in candidates
                                      if row["workout"] == stored_workout]
            candidate_keys = [f"{row['iso_week']}:{row['logical_key']}" for row in candidates]

            observation_id = (existing_observation["id"]
                              if existing_observation is not None else None)
            status = "matched" if observation_id else (
                "ambiguous" if len(candidates) > 1 else "unmatched")
            if existing_observation is not None and len(candidates) == 1:
                observation_for_link: Any = existing_observation
                if existing_observation["started_at"] is None and started_at is not None:
                    connection.execute(observations.update().where(
                        observations.c.id == existing_observation["id"]
                    ).values(started_at=started_at))
                    observation_for_link = {**existing_observation, "started_at": started_at}
                self._link_cycle_observation_in(
                    connection, candidates[0], observation_for_link, actor=actor)
            if observation_id is None and len(candidates) == 1 and started_at is not None:
                candidate = candidates[0]
                kind = self._observation_kind_for_workout(candidate["workout"])
                if kind:
                    observation_id = self.add_observation_in(connection, {
                        "occurred_on": started_at.date(), "started_at": started_at,
                        "kind": kind, "idempotency_key": f"strava:{activity_id}",
                        "strava_activity_id": activity_id,
                        "gear_id": activity.gear_id,
                        "note": f"Strava : {(activity.name or sport).strip()}",
                        "source": actor,
                        "measurements": self._strava_measurements(activity),
                        "session_key": candidate_keys[0],
                    }, actor=actor)
                    status = "matched"

            core_values = {
                "activity_id": activity_id, "athlete_id": self.athlete_id,
                "started_at": started_at, "sport": sport, "distilled": activity.to_json(),
                "status": status, "candidate_session_keys": candidate_keys,
                "observation_id": observation_id, "error": "", "updated_at": sa.func.now(),
            }
            insert_values = {
                **core_values,
                "elevation_loss_state": (
                    "pending" if activity.elevation_gain_m is not None else "unavailable"),
                "wbgt_state": "pending" if activity.weather_query else "unavailable",
            }
            connection.execute(pg_insert(strava_activity_intakes).values(
                **insert_values).on_conflict_do_update(
                    index_elements=[strava_activity_intakes.c.activity_id], set_=core_values))
            aggregate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"strava:{activity_id}")
            _audit(connection, actor, "strava.captured", "strava_activity", aggregate_id,
                   {"status": status, "candidates": candidate_keys,
                    "observation_id": str(observation_id) if observation_id else None})
            return {"activity_id": activity_id, "status": status,
                    "candidate_session_keys": candidate_keys,
                    "observation_id": str(observation_id) if observation_id else None}

    def finish_strava_enrichment(self, activity_id: int, kind: str,
                                 *, value: float | None = None,
                                 unavailable: bool = False, error: str = "",
                                 actor: str = "strava-enrichment") -> str:
        """Persist an enrichment and project it onto the observation, if one exists.

        The intake keeps the result even while the match is still ambiguous, so a later
        match recovers the elevation loss and WBGT without calling the providers again.
        """
        definitions = {
            "elevation_loss": ("elevation_loss_m", int),
            "wbgt": ("wbgt_c", float),
        }
        if kind not in definitions:
            raise StorageError(f"enrichissement Strava inconnu : {kind}")
        metric, cast = definitions[kind]
        state = "failed" if error else "unavailable" if unavailable else "known"
        clean_value = None if state != "known" else cast(value)
        with self.database.transaction() as connection:
            intake = connection.execute(sa.select(strava_activity_intakes).where(
                strava_activity_intakes.c.activity_id == int(activity_id),
                strava_activity_intakes.c.athlete_id == self.athlete_id,
            ).with_for_update()).mappings().one_or_none()
            if intake is None:
                raise StorageError(f"activité Strava non capturée : {activity_id}")
            current_state = intake[f"{kind}_state"]
            current_value = intake[f"{kind}_value"]
            if current_state == "known":
                if clean_value is not None and float(current_value) == float(clean_value):
                    return "known"
                raise StorageError(f"enrichissement {kind} déjà connu avec une autre valeur")
            connection.execute(strava_activity_intakes.update().where(
                strava_activity_intakes.c.activity_id == int(activity_id)).values({
                    f"{kind}_state": state,
                    f"{kind}_value": clean_value,
                    f"{kind}_attempts": intake[f"{kind}_attempts"] + 1,
                    f"{kind}_error": str(error)[:500],
                    "updated_at": sa.func.now(),
                }))
            observation_id = intake["observation_id"]
            payload: dict[str, Any] = {"state": state, "value": clean_value,
                                       "error": str(error)[:500]}
            if observation_id is not None and state in {"known", "unavailable"}:
                stored = connection.execute(sa.select(
                    observation_metrics.c[f"{metric}_state"],
                    observation_metrics.c[metric],
                ).where(observation_metrics.c.observation_id == observation_id)
                ).one()
                if stored[0] == "known":
                    # The coach may have recorded the session before the webhook: its
                    # measurement stays (a known measurement is immutable) and the
                    # discrepancy is only a trace. Raising here would roll back the
                    # whole capture.
                    if clean_value is None or float(stored[1]) != float(clean_value):
                        payload["observation_kept"] = float(stored[1])
                else:
                    self.complete_measurement_in(
                        connection, observation_id, metric, clean_value,
                        unavailable=state == "unavailable", actor=actor)
            aggregate_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"strava:{int(activity_id)}:{kind}")
            _audit(connection, actor, "strava.enriched", "strava_enrichment",
                   aggregate_id, payload)
            return state

    def record_strava_failure(self, activity_id: int, error: str,
                              *, actor: str = "strava-ingestion") -> None:
        safe_error = str(error)[:500]
        with self.database.transaction() as connection:
            values = {
                "activity_id": int(activity_id), "athlete_id": self.athlete_id,
                "sport": "inconnu", "distilled": {}, "status": "failed",
                "candidate_session_keys": [], "error": safe_error,
                "updated_at": sa.func.now(),
            }
            connection.execute(pg_insert(strava_activity_intakes).values(**values).on_conflict_do_update(
                index_elements=[strava_activity_intakes.c.activity_id], set_=values))
            aggregate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"strava:{int(activity_id)}")
            _audit(connection, actor, "strava.capture_failed", "strava_activity",
                   aggregate_id, {"error": safe_error})

    def strava_intakes(self, *, unresolved_only: bool = False) -> list[dict]:
        query = sa.select(*_INTAKE_CORE).where(
            strava_activity_intakes.c.athlete_id == self.athlete_id)
        if unresolved_only:
            query = query.where(strava_activity_intakes.c.status.in_((
                "ambiguous", "unmatched", "failed")))
        with self.database.engine.connect() as connection:
            rows = connection.execute(query.order_by(
                strava_activity_intakes.c.started_at.desc().nullslast(),
                strava_activity_intakes.c.activity_id.desc()).limit(100)).mappings()
            return [{key: _plain(value) for key, value in dict(row).items()} for row in rows]

    def strava_intake(self, activity_id: int) -> dict:
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(*_INTAKE_CORE).where(
                strava_activity_intakes.c.activity_id == int(activity_id),
                strava_activity_intakes.c.athlete_id == self.athlete_id,
            )).mappings().one_or_none()
        if row is None:
            raise StorageError(f"activité Strava non capturée : {activity_id}")
        return {key: _plain(value) for key, value in dict(row).items()}

    def strava_activity_for_observation(self, observation_id: Any) -> int | None:
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(strava_activity_intakes.c.activity_id).where(
                strava_activity_intakes.c.observation_id == observation_id,
                strava_activity_intakes.c.athlete_id == self.athlete_id,
            )).scalar_one_or_none()

    def strava_detail(self, observation_id: Any) -> dict | None:
        """The Strava capture of an observation, with its streams and weather if recorded."""
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(strava_activity_intakes).where(
                strava_activity_intakes.c.observation_id == observation_id,
                strava_activity_intakes.c.athlete_id == self.athlete_id,
            )).mappings().one_or_none()
        return None if row is None else {key: _plain(value) for key, value in dict(row).items()}

    def store_strava_detail(self, activity_id: int, *, streams: dict | None = None,
                            weather: dict | None = None,
                            actor: str = "notebook") -> None:
        """Keep the lightened streams and the weather recorded for the session detail."""
        values: dict[str, Any] = {}
        if streams is not None:
            values["streams"] = streams
        if weather is not None:
            values["weather"] = weather
        if not values:
            return
        with self.database.transaction() as connection:
            updated = connection.execute(sa.update(strava_activity_intakes).where(
                strava_activity_intakes.c.activity_id == int(activity_id),
                strava_activity_intakes.c.athlete_id == self.athlete_id,
            ).values(**values, updated_at=sa.func.now()))
            if updated.rowcount != 1:
                raise StorageError(f"activité Strava non capturée : {activity_id}")
            aggregate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"strava:{int(activity_id)}")
            _audit(connection, actor, "strava.detail_kept", "strava_activity",
                   aggregate_id, {"kept": sorted(values)})

    def observation_feelings(self, observation_id: Any) -> dict:
        """Known RPE, enjoyment and per-zone pain of an observation, without the wait."""
        metrics = ("rpe", "enjoyment")
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(*(
                column for metric in metrics for column in (
                    observation_metrics.c[metric], observation_metrics.c[f"{metric}_state"]))
            ).where(observation_metrics.c.observation_id == observation_id)
            ).mappings().one_or_none()
            if row is None:
                return {}
            pains = {zone: float(reading["value"]) for zone, reading in
                     self._pains_of_in(connection, _uuid(observation_id)).items()
                     if reading["state"] == "known"}
        feelings: dict[str, Any] = {metric: float(row[metric]) for metric in metrics
                                    if row[f"{metric}_state"] == "known"}
        if pains:
            feelings["pains"] = pains
        return feelings

    def strava_enrichment_state(self, activity_id: int) -> dict:
        intake = self.strava_intake(activity_id)
        return {
            kind: {
                "state": intake[f"{kind}_state"],
                "value": intake[f"{kind}_value"],
                "attempts": intake[f"{kind}_attempts"],
                "error": intake[f"{kind}_error"],
            }
            for kind in ("elevation_loss", "wbgt")
        }
    def activity_recorded(self, strava_activity_id: int) -> bool:
        """Has this exact Strava identity landed in the ledger?

        The counterpart of `required_weeks`: a prescriptive turn must prove it revised
        the week, and a debrief must likewise prove it recorded the session it talked
        about.

        This check never proves the absence of the fact: a chat observation can exist
        without having been linked yet. It only proves that the debrief wrote or
        reconciled the Strava identity it received.
        """
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(sa.func.count()).select_from(observations).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.strava_activity_id == int(strava_activity_id),
            )).scalar_one() > 0
