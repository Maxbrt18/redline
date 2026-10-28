"""Capabilities, threads, deadlines, gear, predictions, permits and corrections."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from redline.model.vocabulary import ThreadStatus

from ..database import StorageError
from ..schema import (
    audit_events,
    capability_evaluations,
    coach_predictions,
    conversation_threads,
    correction_deliveries,
    deadlines,
    gear_items,
    observation_metrics,
    observations,
    pain_readings,
    permit_choices,
    proactive_jobs,
)
from ._common import (
    CORRECTION_ANNOUNCE_WINDOW,
    RESOLUTION_COLUMNS,
    StoreBase,
    _audit,
    _jsonable,
    _plain,
    _request_projection,
    _resolution_rule,
    _rule_holds,
    _uuid,
)


class Facts(StoreBase):
    def evaluate_capability(self, row: dict, *, actor: str = "application") -> bool:
        with self.database.transaction() as connection:
            return self.add_capability_evaluation_in(connection, row, actor=actor)

    def add_capability_evaluation_in(self, connection: Connection, row: dict,
                                     *, actor: str = "application") -> bool:
        """One evaluation per capability per day, immutable once written.

        Returns False when the same evaluation already existed (a no-op replay). Different
        content for the same day is rejected so that a conflict never silently drops it.
        """
        evaluation_id = _uuid(row.get("id"))
        values = {
            "capability_number": row["capability_number"], "slug": row["slug"],
            "need": row["need"], "score": row["score"], "scale": row["scale"],
            "status": row["status"], "evaluated_on": row["evaluated_on"],
            "next_test": row.get("next_test"), "lock_reason": row.get("lock_reason"),
        }
        inserted = connection.execute(pg_insert(capability_evaluations).values(
            id=evaluation_id, athlete_id=self.athlete_id, **values,
        ).on_conflict_do_nothing(constraint="uq_capability_evaluations_day").returning(
            capability_evaluations.c.id)).scalar_one_or_none()
        if inserted:
            _audit(connection, actor, "capability.evaluated", "capability", evaluation_id,
                   _jsonable(row))
            return True
        existing = connection.execute(sa.select(capability_evaluations).where(
            capability_evaluations.c.athlete_id == self.athlete_id,
            capability_evaluations.c.capability_number == values["capability_number"],
            capability_evaluations.c.evaluated_on == values["evaluated_on"],
        )).mappings().one()
        differing = [key for key in values if existing[key] != values[key]]
        if differing:
            raise StorageError(
                f"une évaluation existe déjà pour la capacité {values['capability_number']} "
                f"au {values['evaluated_on']} (champs différents : {', '.join(differing)}) ; "
                "elle est immuable : date la nouvelle évaluation du jour où elle est faite")
        return False

    def add_thread_in(self, connection: Connection, row: dict,
                      *, actor: str = "application") -> None:
        thread_id = _uuid(row.get("id"))
        values = {
            "id": thread_id, "athlete_id": self.athlete_id, "thread_key": row["thread_key"],
            "opened_on": row["opened_on"], "kind": row["kind"], "body": row["body"],
            "follow_up_on": row.get("follow_up_on"), "status": row["status"],
            "closed_at": (row.get("closed_at") or dt.datetime.now(dt.UTC)
                          if row["status"] == ThreadStatus.CLOSED else None)}
        actual_id = connection.execute(pg_insert(conversation_threads).values(
            **values).on_conflict_do_update(
            constraint="uq_conversation_threads_key", set_={key: value for key, value in values.items()
                                                if key not in ("id", "athlete_id", "thread_key")}
        ).returning(conversation_threads.c.id)).scalar_one()
        _audit(connection, actor, "thread.upserted", "thread", actual_id, _jsonable(values))
        _request_projection(connection)

    def upsert_thread(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.add_thread_in(connection, row, actor=actor)

    def add_deadline_in(self, connection: Connection, row: dict,
                        *, actor: str = "application") -> None:
        deadline_id = _uuid(row.get("id"))
        values = {
            "id": deadline_id, "athlete_id": self.athlete_id,
            "deadline_key": row["deadline_key"], "due_on": row["due_on"],
            "item": row["item"], "fact_owner": row["fact_owner"],
            "blocks": row.get("blocks"), "status": row["status"],
            "completed_on": row.get("completed_on"),
            "kind": row.get("kind") or "general", "starts_on": row.get("starts_on"),
            "ends_on": row.get("ends_on"), "context": row.get("context") or {},
            "athlete_label": row.get("athlete_label")}
        actual_id = connection.execute(pg_insert(deadlines).values(**values).on_conflict_do_update(
            constraint="uq_deadlines_key", set_={key: value for key, value in values.items()
                                                  if key not in ("id", "athlete_id", "deadline_key")}
        ).returning(deadlines.c.id)).scalar_one()
        _audit(connection, actor, "deadline.upserted", "deadline", actual_id, _jsonable(values))
        _request_projection(connection)

    def upsert_deadline(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.add_deadline_in(connection, row, actor=actor)

    def upsert_gear_in(self, connection: Connection, row: dict,
                       *, actor: str = "application") -> None:
        values = {"gear_id": row["gear_id"], "athlete_id": self.athlete_id,
                  "name": row["name"], "role": row["role"],
                  "retire_at_km": row["retire_at_km"], "status": row.get("status", "active")}
        connection.execute(pg_insert(gear_items).values(**values).on_conflict_do_update(
            index_elements=[gear_items.c.gear_id], set_={
                key: value for key, value in values.items() if key not in ("gear_id", "athlete_id")}))
        aggregate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"gear:{row['gear_id']}")
        _audit(connection, actor, "gear.upserted", "gear", aggregate_id, _jsonable(values))

    def upsert_gear(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.upsert_gear_in(connection, row, actor=actor)

    def sync_strava_gear(self, row: dict, *, actor: str = "strava-gear-sync") -> None:
        """Refresh the provider's distance counter without overwriting the custom role.

        An unknown pair enters with an explicit "to qualify" role and a conservative
        inspection threshold. The first manual setting of those two fields then stays
        authoritative: Strava knows the kilometres, not the intended use.
        """
        gear_id = str(row.get("gear_id") or row.get("id") or "").strip()
        name = str(row.get("name") or row.get("nickname") or "").strip()
        try:
            distance_km = float(row["distance_km"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError("kilométrage Strava du matériel invalide") from exc
        if not gear_id or not name or distance_km < 0:
            raise StorageError("identité ou kilométrage Strava du matériel invalide")
        now = dt.datetime.now(dt.UTC)
        insert_values = {
            "gear_id": gear_id, "athlete_id": self.athlete_id, "name": name,
            "role": "à qualifier", "retire_at_km": 650,
            "strava_distance_km": distance_km, "distance_synced_at": now,
            "primary": bool(row.get("primary")),
            "status": "retired" if bool(row.get("retired")) else "active",
        }
        refreshed = {
            "name": name, "strava_distance_km": distance_km,
            "distance_synced_at": now, "primary": bool(row.get("primary")),
            "status": insert_values["status"],
        }
        with self.database.transaction() as connection:
            connection.execute(pg_insert(gear_items).values(**insert_values).on_conflict_do_update(
                index_elements=[gear_items.c.gear_id], set_=refreshed))
            aggregate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"gear:{gear_id}")
            _audit(connection, actor, "gear.distance_synced", "gear", aggregate_id,
                   _jsonable({key: insert_values[key] for key in (
                       "gear_id", "name", "strava_distance_km", "primary", "status")}))

    def gear_rows(self) -> list[dict]:
        distance = sa.case(
            (observation_metrics.c.distance_km_state == "known",
             observation_metrics.c.distance_km), else_=0)
        local_distance = sa.func.coalesce(sa.func.sum(distance), 0)
        effective_distance = sa.func.greatest(
            local_distance, sa.func.coalesce(gear_items.c.strava_distance_km, 0))
        query = sa.select(
            gear_items, local_distance.label("local_distance_km"),
            effective_distance.label("distance_km")
        ).outerjoin(observations, sa.and_(
            observations.c.gear_id == gear_items.c.gear_id,
            observations.c.athlete_id == gear_items.c.athlete_id,
        )).outerjoin(observation_metrics,
                    observation_metrics.c.observation_id == observations.c.id).where(
            gear_items.c.athlete_id == self.athlete_id).group_by(gear_items.c.gear_id)
        with self.database.engine.connect() as connection:
            return [{key: _plain(value) for key, value in dict(row).items()}
                    for row in connection.execute(query).mappings()]

    def upsert_prediction_in(self, connection: Connection, row: dict,
                             *, actor: str = "application") -> None:
        operation = row.get("operation", "create")
        existing = connection.execute(sa.select(coach_predictions).where(
            coach_predictions.c.athlete_id == self.athlete_id,
            coach_predictions.c.prediction_key == row["prediction_key"],
        ).with_for_update()).mappings().one_or_none()
        if operation == "create":
            values = {"id": _uuid(row.get("id")), "athlete_id": self.athlete_id,
                      "prediction_key": row["prediction_key"], "domain": row["domain"],
                      "statement": row["statement"], "probability": row["probability"],
                      "due_on": row["due_on"], "direction": row.get("direction", "neutral"),
                      **_resolution_rule(row.get("resolves_on"), row["due_on"])}
            if existing:
                if values["resolves_kind"] is not None and values["resolves_zone"] is None:
                    values["resolves_zone"] = existing["resolves_zone"]
                comparable = ("domain", "statement", "probability", "due_on", "direction",
                              *RESOLUTION_COLUMNS)
                if any(_plain(existing[key]) != _plain(values[key]) for key in comparable):
                    raise StorageError(
                        f"collision d'idempotence pour la prédiction {row['prediction_key']!r}")
                return
            if values["resolves_kind"] is not None:
                values["resolves_zone"] = self._prediction_zone_in(
                    connection, values["resolves_zone"])
            if values["resolves_kind"] is not None and self._observed_value_in(
                    connection, values["resolves_kind"], values["resolves_on"],
                    values["resolves_zone"]) is not None:
                raise StorageError(
                    f"l'observation {values['resolves_kind']} du {values['resolves_on']} "
                    "existe déjà : une prédiction s'écrit avant son issue")
            connection.execute(coach_predictions.insert().values(**values))
            aggregate_id = values["id"]
            action = "prediction.created"
        elif operation == "resolve":
            if existing is None:
                raise StorageError(f"prédiction inconnue : {row['prediction_key']}")
            if existing["status"] != "open":
                return
            now = dt.datetime.now(dt.UTC)
            if row.get("void"):
                updates = {"status": "void", "resolved_at": now}
            else:
                outcome = bool(row["outcome"])
                probability = float(existing["probability"])
                updates = {"status": "resolved", "outcome": outcome,
                           "brier_score": (probability - float(outcome)) ** 2,
                           "resolved_at": now}
            updates["outcome_reason"] = row.get("reason")
            connection.execute(coach_predictions.update().where(
                coach_predictions.c.id == existing["id"]).values(**updates))
            aggregate_id = existing["id"]
            action = "prediction.resolved"
        elif operation == "rectify":
            # A prediction settled while its condition was not met, or without the
            # observation meant to settle it, was never tested: it becomes `void`, with
            # a reason. Never the reverse, and never true <-> false: a score flipped
            # after the fact is no longer a calibration.
            if existing is None:
                raise StorageError(f"prédiction inconnue : {row['prediction_key']}")
            if existing["status"] == "void":
                return
            if existing["status"] != "resolved":
                raise StorageError("seule une prédiction soldée se rectifie ; "
                                   "une prédiction ouverte se solde avec --outcome void")
            if not (row.get("reason") or "").strip():
                raise StorageError("une rectification exige son motif")
            connection.execute(coach_predictions.update().where(
                coach_predictions.c.id == existing["id"]).values(
                    status="void", outcome=None, brier_score=None, outcome_reason=None))
            aggregate_id = existing["id"]
            action = "prediction.rectified"
        elif operation == "challenge":
            if existing is None:
                raise StorageError(f"prédiction inconnue : {row['prediction_key']}")
            connection.execute(coach_predictions.update().where(
                coach_predictions.c.id == existing["id"]).values(
                    challenge=row["challenge"], challenged_at=dt.datetime.now(dt.UTC)))
            aggregate_id = existing["id"]
            action = "prediction.challenged"
        else:
            raise StorageError(f"opération de prédiction inconnue : {operation}")
        _audit(connection, actor, action, "prediction", aggregate_id, _jsonable(row))

    def _prediction_zone_in(self, connection: Connection, zone: str | None) -> str:
        """The zone the prediction expects: the named one, or the only monitored zone."""
        active = self._active_zones_in(connection)
        if zone is None:
            if len(active) != 1:
                raise StorageError(
                    "une prédiction tranchée par le réveil nomme sa zone ("
                    + (", ".join(active) or "aucune zone surveillée") + ")")
            return active[0]
        if zone not in active:
            raise StorageError(f"zone de douleur inconnue ou inactive : {zone}")
        return zone

    def _observed_value_in(self, connection: Connection, kind: str,
                           day: dt.date, zone: str) -> float | None:
        """The latest known value, in this zone, of the settling observation."""
        value = connection.execute(sa.select(pain_readings.c.value).join(
            observations, observations.c.id == pain_readings.c.observation_id).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.kind == kind, observations.c.occurred_on == day,
                pain_readings.c.zone == zone, pain_readings.c.state == "known",
            ).order_by(observations.c.created_at.desc()).limit(1)).scalar_one_or_none()
        return None if value is None else float(value)

    def settle_observed_predictions_in(self, connection: Connection, kind: str,
                                       day: dt.date, zone: str, value: float | None,
                                       *, actor: str = "application") -> int:
        """Settle the open predictions that were waiting for this observation, in its zone.

        `value=None`: the observation is declared unavailable or never came; the
        prediction was not tested, so it becomes `void`, never false.
        """
        bound = connection.execute(sa.select(coach_predictions).where(
            coach_predictions.c.athlete_id == self.athlete_id,
            coach_predictions.c.status == "open",
            coach_predictions.c.resolves_kind == kind,
            coach_predictions.c.resolves_on == day,
            coach_predictions.c.resolves_zone == zone,
        ).with_for_update()).mappings().all()
        for prediction in bound:
            observed = {"kind": kind, "date": day.isoformat(), "zone": zone, "value": value}
            if value is None:
                payload = {"void": True}
            else:
                payload = {"outcome": _rule_holds(prediction["success_op"],
                                                  float(prediction["success_value"]), value)}
            self.upsert_prediction_in(connection, {
                "prediction_key": prediction["prediction_key"], "operation": "resolve",
                "observed": observed, **payload}, actor=actor)
        return len(bound)

    def settle_unobserved_predictions_in(self, connection: Connection, today: dt.date,
                                         *, actor: str = "application") -> int:
        """Close the predictions whose observation date has passed.

        An observation that arrived without triggering settlement (written before the
        binding) still settles; its absence makes the prediction `void`.
        """
        pending = connection.execute(sa.select(
            coach_predictions.c.resolves_kind, coach_predictions.c.resolves_on,
            coach_predictions.c.resolves_zone,
        ).where(
            coach_predictions.c.athlete_id == self.athlete_id,
            coach_predictions.c.status == "open",
            coach_predictions.c.resolves_kind.is_not(None),
            coach_predictions.c.resolves_on < today,
        ).distinct()).all()
        return sum(self.settle_observed_predictions_in(
            connection, kind, day, zone,
            self._observed_value_in(connection, kind, day, zone), actor=actor)
            for kind, day, zone in pending)

    def upsert_prediction(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.upsert_prediction_in(connection, row, actor=actor)

    def prediction_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            return [{key: _plain(value) for key, value in dict(row).items()}
                    for row in connection.execute(sa.select(coach_predictions).where(
                        coach_predictions.c.athlete_id == self.athlete_id).order_by(
                            coach_predictions.c.due_on.desc())).mappings()]

    def seal_permit_choice_in(self, connection: Connection, row: dict,
                              *, actor: str = "application") -> None:
        """Seal the coach's choice and its prediction in the same transaction.

        The choice is immutable: replaying the same write does nothing, a write that
        would change it is rejected. The linked prediction is what makes it falsifiable.
        """
        scheduled_on = row["scheduled_on"]
        if isinstance(scheduled_on, str):
            scheduled_on = dt.date.fromisoformat(scheduled_on)
        values = {"athlete_id": self.athlete_id, "domain": "easy",
                  "iso_week": row["iso_week"], "session_key": row["session_key"],
                  "scheduled_on": scheduled_on, "coach_minutes": int(row["coach_minutes"]),
                  "prediction_key": row["prediction_key"]}
        existing = connection.execute(sa.select(permit_choices).where(
            permit_choices.c.athlete_id == self.athlete_id,
            permit_choices.c.iso_week == values["iso_week"],
            permit_choices.c.session_key == values["session_key"],
        ).with_for_update()).mappings().one_or_none()
        if existing is not None:
            if any(_plain(existing[key]) != _plain(values[key]) for key in values):
                raise StorageError(
                    f"choix déjà scellé pour {values['session_key']} : il ne se corrige pas")
            return
        values["id"] = _uuid(row.get("id"))
        connection.execute(permit_choices.insert().values(**values))
        self.upsert_prediction_in(connection, {
            "operation": "create", "prediction_key": values["prediction_key"],
            "domain": "permis-ef", "statement": row["statement"],
            "probability": row["probability"], "due_on": row["due_on"]
            if not isinstance(row["due_on"], str) else dt.date.fromisoformat(row["due_on"]),
            "direction": "neutral"}, actor=actor)
        _audit(connection, actor, "permit.sealed", "permit_choice", values["id"],
               _jsonable({**values, "id": str(values["id"])}))

    def seal_permit_choice(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.seal_permit_choice_in(connection, row, actor=actor)

    def permit_choice_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            return [{key: _plain(value) for key, value in dict(row).items()}
                    for row in connection.execute(sa.select(permit_choices).where(
                        permit_choices.c.athlete_id == self.athlete_id).order_by(
                            permit_choices.c.scheduled_on)).mappings()]

    def register_correction_in(self, connection: Connection, row: dict,
                               *, actor: str = "application") -> None:
        values = {"id": _uuid(row.get("id")), "athlete_id": self.athlete_id,
                  "correction_key": row["correction_key"], "summary": row["summary"]}
        existing = connection.execute(sa.select(correction_deliveries).where(
            correction_deliveries.c.athlete_id == self.athlete_id,
            correction_deliveries.c.correction_key == row["correction_key"],
        ).with_for_update()).mappings().one_or_none()
        if existing and existing["summary"] == row["summary"]:
            return
        if existing:
            now = dt.datetime.now(dt.UTC)
            legacy_key = f"correction:{row['correction_key']}"
            revision_prefix = f"correction-id:{existing['id']}:"
            jobs = connection.execute(sa.select(proactive_jobs).where(sa.or_(
                proactive_jobs.c.job_key == legacy_key,
                proactive_jobs.c.job_key.like(revision_prefix + "%"),
            )).with_for_update()).mappings().all()
            if any(job["status"] == "running" for job in jobs):
                raise StorageError(
                    "correction en cours d'annonce : réessaie après sa livraison")
            connection.execute(correction_deliveries.update().where(
                correction_deliveries.c.id == existing["id"]).values(
                    summary=row["summary"], registered_at=now, delivered_at=None))
            if jobs:
                connection.execute(proactive_jobs.update().where(
                    proactive_jobs.c.job_key.in_([job["job_key"] for job in jobs])).values(
                        status="done", completed_at=now))
            revision_job = f"{revision_prefix}{int(now.timestamp() * 1_000_000)}"
            connection.execute(proactive_jobs.insert().values(
                job_key=revision_job, kind="correction", detail=row["summary"],
                due_at=now, expires_at=now + CORRECTION_ANNOUNCE_WINDOW, priority=True))
            aggregate_id = existing["id"]
            action = "correction.revised"
        else:
            connection.execute(correction_deliveries.insert().values(**values))
            aggregate_id = values["id"]
            action = "correction.registered"
        _audit(connection, actor, action, "correction", aggregate_id, _jsonable(values))

    def register_correction(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.register_correction_in(connection, row, actor=actor)

    def correction_rows(self) -> list[dict]:
        """The corrections, with the number of recurrences and the date of the first.

        A recurrence replaces the summary under the same key; without this count the row
        would no longer show that a mistake came back. The audit log keeps every version,
        so counting it is enough.
        """
        events = sa.select(
            audit_events.c.aggregate_id,
            sa.func.count().filter(audit_events.c.action == "correction.revised")
            .label("revisions"),
            sa.func.min(audit_events.c.created_at).label("first_registered_at"),
        ).where(audit_events.c.aggregate_type == "correction").group_by(
            audit_events.c.aggregate_id).subquery()
        with self.database.engine.connect() as connection:
            return [{key: _plain(value) for key, value in dict(row).items()}
                    for row in connection.execute(sa.select(
                        correction_deliveries,
                        sa.func.coalesce(events.c.revisions, 0).label("revisions"),
                        events.c.first_registered_at,
                    ).outerjoin(events, events.c.aggregate_id == correction_deliveries.c.id).where(
                        correction_deliveries.c.athlete_id == self.athlete_id).order_by(
                            correction_deliveries.c.registered_at.desc())).mappings()]
    def latest_capabilities(self) -> list[dict]:
        ranked = sa.select(
            capability_evaluations,
            sa.func.row_number().over(
                partition_by=capability_evaluations.c.capability_number,
                order_by=(capability_evaluations.c.evaluated_on.desc(),
                          capability_evaluations.c.created_at.desc()),
            ).label("rank"),
        ).where(capability_evaluations.c.athlete_id == self.athlete_id).subquery()
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(ranked).where(
                ranked.c.rank == 1).order_by(ranked.c.capability_number)).mappings()]

    def thread_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(
                conversation_threads).where(
                    conversation_threads.c.athlete_id == self.athlete_id
                ).order_by(conversation_threads.c.opened_on.desc())).mappings()]

    def deadline_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(deadlines).where(
                deadlines.c.athlete_id == self.athlete_id
            ).order_by(deadlines.c.due_on, deadlines.c.deadline_key)).mappings()]
