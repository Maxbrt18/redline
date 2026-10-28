"""Les séances de renforcement et leurs rapports."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from scripts.model import session_kinds

from ..database import StorageError
from ..schema import (
    inbound_activity_states,
    inbound_messages,
    planned_sessions,
    proactive_jobs,
    session_cycles,
    strength_pain_readings,
    strength_report_claims,
    strength_report_daily_quotas,
    strength_results,
    strength_sessions,
    strength_sets,
)
from ._common import StoreBase, _audit, _request_projection, _uuid


class Strength(StoreBase):
    def strength_evidence_in(self, connection: Connection, day: dt.date,
                             routine: str | None) -> list:
        """Rapports compatibles ; leur présence n'identifie pas seule une séance."""
        if not routine:
            return []
        return list(connection.execute(sa.select(strength_sessions.c.id).where(
            strength_sessions.c.athlete_id == self.athlete_id,
            strength_sessions.c.performed_on == day,
            strength_sessions.c.workout == routine,
        )).scalars())

    def reconcile_strength_cycles_in(self, connection: Connection, *,
                                      actor: str = "strength-reconciliation") -> int:
        """Raccorde seulement les correspondances bijectives date/type de renfo.

        Aucun doublon de registre ni heure de fin inventée. Une ambiguïté conserve
        les faits tels quels et interdit d'en déduire une absence de séance.
        """
        rows = connection.execute(sa.select(
            session_cycles, planned_sessions.c.scheduled_on, planned_sessions.c.routine,
        ).join(planned_sessions,
               planned_sessions.c.id == session_cycles.c.source_session_id).where(
            session_cycles.c.athlete_id == self.athlete_id,
            session_cycles.c.state != "cancelled",
            planned_sessions.c.workout == session_kinds.STRENGTH,
        ).order_by(session_cycles.c.id).with_for_update(of=session_cycles)).mappings().all()
        groups: dict[tuple, list] = {}
        for row in rows:
            groups.setdefault((row["scheduled_on"], row["routine"]), []).append(row)
        changed = 0
        now = dt.datetime.now(dt.UTC)
        for (day, routine), candidates in groups.items():
            reports = self.strength_evidence_in(connection, day, routine)
            if len(candidates) != 1 or len(reports) != 1:
                continue
            row = candidates[0]
            if row["state"] not in {"planned", "started", "missed"}:
                continue
            connection.execute(session_cycles.update().where(
                session_cycles.c.id == row["id"]).values(state="completed", updated_at=now))
            connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == f"missed-session:{row['id']}",
                proactive_jobs.c.status.in_(("pending", "running")),
            ).values(status="done", completed_at=now))
            _audit(connection, actor, "session.completed.strength", "session_cycle", row["id"],
                   {"strength_session_id": str(reports[0]), "match": "unique-date-workout"})
            changed += 1
        if changed:
            _request_projection(connection)
        return changed

    def accept_strength_report(self, payload: dict, max_per_day: int, *,
                               actor: str = "strength-page") -> str:
        """Persiste séance et message durable ; renvoie accepted, duplicate ou quota."""
        today = payload["performed_on"]
        session_key = payload["session_key"]
        with self.database.transaction() as connection:
            connection.execute(sa.text(
                "SELECT pg_advisory_xact_lock(hashtext('redline:strength-report-quota'))"))
            already_claimed = connection.execute(sa.select(
                strength_report_claims.c.session_key).where(
                    strength_report_claims.c.session_key == session_key)).scalar_one_or_none()
            if already_claimed is not None:
                return "duplicate"
            count = connection.execute(sa.select(
                strength_report_daily_quotas.c.accepted_count).where(
                    strength_report_daily_quotas.c.quota_on == today
                ).with_for_update()).scalar_one_or_none() or 0
            if count >= max_per_day:
                return "quota"
            claimed = connection.execute(pg_insert(strength_report_claims).values(
                session_key=session_key).on_conflict_do_nothing().returning(
                    strength_report_claims.c.session_key)).scalar_one_or_none()
            if claimed is None:
                return "duplicate"
            if not self._add_strength_session_in(connection, payload, actor=actor):
                connection.execute(strength_report_claims.delete().where(
                    strength_report_claims.c.session_key == session_key))
                return "duplicate"
            connection.execute(inbound_messages.insert().values(
                message_id=payload["message_id"], psid=payload["psid"],
                kind="postback", body=payload["message"],
            ))
            now = dt.datetime.now(dt.UTC)
            connection.execute(pg_insert(inbound_activity_states).values(
                psid=payload["psid"], last_inbound_at=now,
            ).on_conflict_do_update(
                index_elements=[inbound_activity_states.c.psid],
                set_={"last_inbound_at": now}))
            connection.execute(pg_insert(strength_report_daily_quotas).values(
                quota_on=today, accepted_count=count + 1,
            ).on_conflict_do_update(
                index_elements=[strength_report_daily_quotas.c.quota_on],
                set_={"accepted_count": count + 1}))
            return "accepted"

    def _add_strength_session_in(self, connection: Connection, payload: dict, *,
                                 actor: str) -> bool:
        session_id = _uuid(payload.get("id"))
        inserted = connection.execute(pg_insert(strength_sessions).values(
            id=session_id,
            athlete_id=payload.get("athlete_id", self.athlete_id),
            session_key=payload["session_key"],
            performed_on=payload.get("performed_on", payload.get("day")),
            workout=payload.get("workout"),
            duration_minutes=payload.get("duration_minutes"),
            note=payload.get("note", ""),
            prescription=payload.get("prescription"),
        ).on_conflict_do_nothing(
            constraint="uq_strength_sessions_key").returning(strength_sessions.c.id)
        ).scalar_one_or_none()
        if inserted is None:
            return False
        active = self._active_zones_in(connection)
        for zone, moments in (payload.get("pains") or {}).items():
            if zone not in active:
                raise StorageError(f"zone de douleur inconnue ou inactive : {zone}")
            for moment in ("before", "after"):
                if moments.get(moment) is not None:
                    connection.execute(strength_pain_readings.insert().values(
                        strength_session_id=session_id, zone=zone, moment=moment,
                        athlete_id=payload.get("athlete_id", self.athlete_id),
                        value=moments[moment]))
        for position, exercise in enumerate(payload.get("exercises", []), start=1):
            result_id = _uuid(exercise.get("id"))
            connection.execute(strength_results.insert().values(
                id=result_id, session_id=session_id,
                position=position,
                exercise_slug=exercise["exercise_slug"],
                difficulty=exercise.get("difficulty"),
                skipped=bool(exercise.get("skipped")),
                status=exercise.get("status"),
                completed_slots=exercise.get("completed_slots"),
                note=exercise.get("note", ""),
            ))
            for series in exercise.get("sets", []):
                connection.execute(strength_sets.insert().values(
                    result_id=result_id, side=series.get("side", "both"),
                    set_index=series["set_index"], repetitions=series["repetitions"],
                ))
        _audit(connection, actor, "strength.created", "strength_session", session_id,
               {"session_key": payload["session_key"]})
        self.reconcile_strength_cycles_in(connection, actor=actor)
        _request_projection(connection)
        return True

    def strength_history(self) -> list[dict]:
        query = sa.select(
            strength_sessions.c.id.label("session_id"), strength_sessions.c.session_key,
            strength_sessions.c.performed_on, strength_sessions.c.workout,
            strength_sessions.c.duration_minutes, strength_sessions.c.note,
            strength_results.c.id.label("result_id"), strength_results.c.position,
            strength_results.c.exercise_slug,
            strength_results.c.difficulty, strength_results.c.skipped,
            strength_results.c.status, strength_results.c.completed_slots,
            strength_results.c.note.label("exercise_note"),
            strength_sets.c.side, strength_sets.c.set_index, strength_sets.c.repetitions,
        ).select_from(strength_sessions).outerjoin(
            strength_results, strength_results.c.session_id == strength_sessions.c.id
        ).outerjoin(
            strength_sets, strength_sets.c.result_id == strength_results.c.id
        ).where(
            strength_sessions.c.athlete_id == self.athlete_id,
        ).order_by(strength_sessions.c.performed_on, strength_sessions.c.created_at,
                   strength_results.c.position, strength_sets.c.side,
                   strength_sets.c.set_index)
        with self.database.engine.connect() as connection:
            sessions: dict[uuid.UUID, dict] = {}
            results: dict[uuid.UUID, dict] = {}
            for row in connection.execute(query).mappings():
                session = sessions.setdefault(row["session_id"], {
                    "id": row["session_id"], "session_key": row["session_key"],
                    "performed_on": row["performed_on"], "workout": row["workout"],
                    "pains": {}, "duration_minutes": row["duration_minutes"],
                    "note": row["note"], "exercises": []})
                if row["result_id"] is None:
                    continue
                result = results.get(row["result_id"])
                if result is None:
                    result = {"id": row["result_id"], "position": row["position"],
                              "exercise_slug": row["exercise_slug"],
                              "difficulty": row["difficulty"], "skipped": row["skipped"],
                              "status": row["status"], "completed_slots": row["completed_slots"],
                              "note": row["exercise_note"],
                              "sets": []}
                    results[row["result_id"]] = result
                    session["exercises"].append(result)
                if row["side"]:
                    result["sets"].append({"side": row["side"],
                                           "set_index": row["set_index"],
                                           "repetitions": row["repetitions"]})
            for reading in connection.execute(sa.select(strength_pain_readings).where(
                    strength_pain_readings.c.athlete_id == self.athlete_id)).mappings():
                owner = sessions.get(reading["strength_session_id"])
                if owner is not None:
                    owner["pains"].setdefault(reading["zone"], {"before": None, "after": None})[
                        reading["moment"]] = reading["value"]
            return list(sessions.values())

    def previous_strength_prescription(self, session: dict, today: dt.date) -> dict | None:
        """Dernière prescription du même type rapportée avant l'ouverture courante."""
        query = sa.select(strength_sessions.c.prescription).where(
            strength_sessions.c.athlete_id == self.athlete_id,
            strength_sessions.c.workout == session["session"],
            strength_sessions.c.session_key != session["sid"],
            strength_sessions.c.performed_on <= today,
        ).order_by(strength_sessions.c.performed_on.desc(), strength_sessions.c.created_at.desc()).limit(1)
        with self.database.engine.connect() as connection:
            return connection.execute(query).scalar_one_or_none()
