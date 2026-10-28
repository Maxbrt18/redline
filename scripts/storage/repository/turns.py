"""Un tour d'agent : ses commandes préparées, appliquées ensemble ou jetées."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from scripts.model import commands, ledger

from ..database import StorageError
from ..schema import (
    conversation_messages,
    conversation_questions,
    observations,
    turn_commands,
)
from ._common import StoreBase, _audit, _jsonable, _uuid


class TurnCommands(StoreBase):
    def stage_command(self, turn_id: str, command_key: str, kind: str,
                      payload: dict) -> uuid.UUID:
        """Enregistre une intention seulement si le tour entier reste applicable.

        Chaque commande préparée est rejouée avec celles qui la précèdent dans un
        SAVEPOINT. Le rejeu est rembobiné avant le commit : le domaine ne bouge pas,
        mais une incohérence revient immédiatement à la CLI et la commande fautive
        n'entre jamais dans le tour. `apply_turn_with_receipts` répète le contrôle au commit final.
        """
        command_id = uuid.uuid4()
        canonical_payload = _jsonable(payload)
        statement = pg_insert(turn_commands).values(
            id=command_id, turn_id=_uuid(turn_id), command_key=command_key,
            kind=kind, payload=canonical_payload,
        ).on_conflict_do_update(index_elements=[turn_commands.c.command_key], set_={
            "command_key": command_key,
        }).returning(turn_commands.c.id, turn_commands.c.turn_id,
                     turn_commands.c.kind, turn_commands.c.payload)
        with self.database.transaction() as connection:
            row = connection.execute(statement).mappings().one()
            if (row["turn_id"] != _uuid(turn_id) or row["kind"] != kind
                    or row["payload"] != canonical_payload):
                raise StorageError(
                    f"collision d'idempotence pour la commande {command_key!r}")
            dry_run = connection.begin_nested()
            try:
                self._apply_turn_in(connection, turn_id)
            finally:
                dry_run.rollback()
            return row["id"]

    def preview_command(self, kind: str, payload: dict, *, turn_id: str = "") -> int:
        """Teste avec les commandes en attente, sans conserver aucun effet.

        Le même moteur contrôle la simulation et l'application. Le SAVEPOINT
        englobe aussi la commande temporaire, l'audit et les projections. Une
        simulation n'est pas une réservation : l'application revérifie l'état.
        """
        actual_turn_id = turn_id or str(uuid.uuid4())
        with self.database.transaction() as connection:
            preview = connection.begin_nested()
            try:
                connection.execute(turn_commands.insert().values(
                    id=uuid.uuid4(), turn_id=_uuid(actual_turn_id),
                    command_key=f"preview:{uuid.uuid4()}", kind=kind,
                    payload=_jsonable(payload),
                ))
                return self._apply_turn_in(connection, actual_turn_id)
            finally:
                preview.rollback()

    def apply_turn_with_receipts(self, turn_id: str) -> tuple[int, tuple[dict, ...]]:
        """Applique tout le tour dans l'unique transaction canonique, puis relit ses reçus.

        Les reçus ne sortent qu'après le commit. Un rejeu conserve les preuves
        déjà enregistrées, mais son compteur de nouvelles applications reste nul.

        **Les faits sont inconditionnels.** Une séance, une mesure, un état du jour
        sont de la vérité terrain ; le programme hebdomadaire en est un CALCUL. Jusqu'au
        2026-08-14 cette méthode annulait les faits quand le plan qui en dérive était
        périmé — elle jetait la séance pour protéger le calcul, exactement à l'envers
        de la doctrine du vault (« une séance non consignée est perdue », « l'état
        dérivé se recalcule »). Ce jour-là, deux débriefs corrects ont été détruits et
        L'athlète a reçu de la plomberie à la place de son coaching.

        La péremption d'un plan n'est donc plus une erreur d'écriture : c'est un VERDICT
        DE PRESCRIPTIBILITÉ, rendu par `VaultRepo.validate_weekly_plan`, qui retient la
        prescription sans toucher ni aux faits ni à la conversation.

        Ce qui reste refusé ici est ce que ce tour écrit MAL : une révision de plan
        invalide n'entre pas en base, parce que l'écriture elle-même est fautive.
        """
        with self.database.transaction() as connection:
            applied = self._apply_turn_in(connection, turn_id)
            rows = connection.execute(sa.select(
                turn_commands.c.command_key, turn_commands.c.kind,
                turn_commands.c.payload, turn_commands.c.applied_at,
            ).where(turn_commands.c.turn_id == _uuid(turn_id),
                    turn_commands.c.applied_at.is_not(None))
              .order_by(turn_commands.c.created_at, turn_commands.c.id)).mappings()
            receipts = tuple({
                "command_key": row["command_key"], "kind": row["kind"],
                "payload": row["payload"], "applied_at": row["applied_at"].isoformat(),
            } for row in rows)
        return applied, receipts

    def pending_turn_kinds(self, turn_id: str) -> tuple[str, ...]:
        """Les types de commandes encore non appliquées d'un tour, dans l'ordre."""
        with self.database.engine.connect() as connection:
            kinds = connection.execute(sa.select(turn_commands.c.kind).where(
                turn_commands.c.turn_id == _uuid(turn_id),
                turn_commands.c.applied_at.is_(None),
            ).order_by(turn_commands.c.created_at, turn_commands.c.id)).scalars()
            return tuple(dict.fromkeys(kinds))

    def _apply_turn_in(self, connection: Connection, turn_id: str) -> int:
        rows = list(connection.execute(sa.select(turn_commands).where(
            turn_commands.c.turn_id == _uuid(turn_id),
            turn_commands.c.applied_at.is_(None),
        ).order_by(turn_commands.c.created_at, turn_commands.c.id).with_for_update()).mappings())
        rows.sort(key=lambda row: row["kind"] == commands.PLAN_REVISE)
        handlers = {
            commands.OBSERVATION_ADD: self._apply_observation,
            commands.ACTIVITY_LINK: self._apply_activity_link,
            commands.MEASUREMENT_COMPLETE: self._apply_measurement,
            commands.HR_DOUBT: self._apply_hr_doubt,
            commands.EXPOSURE_UPSERT: self._apply_exposure,
            commands.PLAN_REVISE: self._apply_plan,
            commands.DAILY_UPSERT: self._apply_daily,
            commands.CAPABILITY_EVALUATE: self._apply_capability,
            commands.THREAD_UPSERT: self._apply_thread,
            commands.QUESTION_REVIEW: self._apply_question_review,
            commands.QUESTION_ASK: self._apply_question_ask,
            commands.DEADLINE_UPSERT: self._apply_deadline,
            commands.SESSION_START: self._apply_session_start,
            commands.SESSION_MATCH: self._apply_session_match,
            commands.PROACTIVE_ANGLE: self._apply_proactive_angle,
            commands.GEAR_UPSERT: self._apply_gear,
            commands.PREDICTION_UPSERT: self._apply_prediction,
            commands.CORRECTION_REGISTER: self._apply_correction,
            commands.PERMIT_SEAL: self._apply_permit_seal,
        }
        applied = 0
        revised_weeks: list[str] = []
        for row in rows:
            payload = dict(row["payload"])
            handler = handlers.get(row["kind"])
            if handler is None:
                raise StorageError(f"commande de tour inconnue : {row['kind']}")
            try:
                revised = handler(connection, payload, f"turn:{turn_id}")
            except (KeyError, TypeError, ValueError) as exc:
                raise StorageError(
                    f"commande {row['kind']} invalide : {exc}") from exc
            if revised:
                revised_weeks.append(revised)
            connection.execute(turn_commands.update().where(
                turn_commands.c.id == row["id"]).values(applied_at=sa.func.now()))
            applied += 1
        # Seules les semaines que CE tour a révisées sont relues : écrire un plan
        # invalide est une faute d'écriture. L'état d'une semaine qu'on n'a pas
        # touchée ne regarde pas les faits qu'on vient d'enregistrer.
        from scripts.model import weekly_plan as weekly_plan_model
        for week in revised_weeks:
            plan = self._load_plan_connection(connection, week)
            if plan is None:
                raise StorageError(f"programme {week} absent après sa révision")
            try:
                weekly_plan_model.validate(plan)
            except Exception as exc:
                raise StorageError(f"révision refusée, programme {week} invalide : {exc}") from exc
        return applied

    def _apply_observation(self, connection: Connection, payload: dict,
                           actor: str) -> None:
        payload["occurred_on"] = dt.date.fromisoformat(payload["occurred_on"])
        if isinstance(payload.get("started_at"), str):
            payload["started_at"] = dt.datetime.fromisoformat(payload["started_at"])
        self.add_observation_in(connection, payload, actor=actor)

    def _apply_activity_link(self, connection: Connection, payload: dict,
                             actor: str) -> None:
        self.link_activity_in(connection, payload["idempotency_key"],
                              int(payload["strava_activity_id"]), actor=actor)

    def _apply_measurement(self, connection: Connection, payload: dict,
                           actor: str) -> None:
        observation_id = connection.execute(sa.select(observations.c.id).where(
            observations.c.athlete_id == self.athlete_id,
            observations.c.idempotency_key == payload["idempotency_key"],
        )).scalar_one_or_none()
        if observation_id is None:
            raise StorageError(f"observation inconnue : {payload['idempotency_key']}")
        self.complete_measurement_in(
            connection, observation_id, payload["metric"], payload.get("value"),
            zone=payload.get("zone"), unavailable=bool(payload.get("unavailable")),
            actor=actor)

    def _apply_hr_doubt(self, connection: Connection, payload: dict, actor: str) -> None:
        observation_id = connection.execute(sa.select(observations.c.id).where(
            observations.c.athlete_id == self.athlete_id,
            observations.c.idempotency_key == payload["idempotency_key"],
        )).scalar_one_or_none()
        if observation_id is None:
            raise StorageError(f"observation inconnue : {payload['idempotency_key']}")
        self.doubt_hr_in(connection, observation_id, payload["reason"], payload["note"],
                         actor=actor)

    def _apply_exposure(self, connection: Connection, payload: dict,
                        actor: str) -> None:
        self.upsert_exposure_in(connection, payload, actor=actor)

    def _apply_plan(self, connection: Connection, payload: dict, actor: str) -> str:
        revision = dict(payload["revision"])
        revision["computed_at"] = dt.datetime.fromisoformat(revision["computed_at"])
        for session in revision.get("sessions", []):
            session["date"] = dt.date.fromisoformat(session["date"])
        for gate in revision.get("gates", []):
            gate["due"] = dt.date.fromisoformat(gate["due"])
        week = payload["week"]
        self.append_plan_revision_in(connection, week, revision, actor=actor)
        return week

    def _apply_daily(self, connection: Connection, payload: dict, actor: str) -> None:
        payload["day"] = dt.date.fromisoformat(payload["day"])
        self.put_daily_in(connection, payload, actor=actor)

    def _apply_capability(self, connection: Connection, payload: dict,
                          actor: str) -> None:
        payload["evaluated_on"] = dt.date.fromisoformat(payload["evaluated_on"])
        self.add_capability_evaluation_in(connection, payload, actor=actor)

    def _apply_proactive_angle(self, connection: Connection, payload: dict,
                               actor: str) -> None:
        """L'angle n'a rien à muter : la commande EST le dépôt.

        Le propriétaire de l'angle livré est `proactive_messages.angle`, et cette
        ligne-là n'existe qu'APRÈS la livraison Messenger — bien après la frontière de
        persistance. La commande de tour sert donc de consigne : `turn_angle()` la
        relit au moment d'écrire la trace. L'appliquer, c'est l'acquitter, et un tour
        abandonné l'emporte avec lui, ce qui est exactement le comportement voulu.
        """
        if not str(payload.get("angle", "")).strip():
            raise ValueError("angle vide")

    def turn_angle(self, turn_id: str) -> str:
        """L'angle déposé par ce tour, ou la chaîne vide s'il n'en a pas déposé."""
        if not turn_id:
            return ""
        with self.database.transaction() as connection:
            row = connection.execute(sa.select(turn_commands.c.payload).where(
                turn_commands.c.turn_id == _uuid(turn_id),
                turn_commands.c.kind == commands.PROACTIVE_ANGLE,
            ).order_by(turn_commands.c.created_at.desc()).limit(1)).scalar_one_or_none()
        return str((row or {}).get("angle", "")).strip()

    def _apply_question_ask(self, connection: Connection, payload: dict, actor: str) -> None:
        """Déclaration seulement ; les marqueurs attendent la livraison du texte exact."""
        text = payload.get("text")
        if not isinstance(text, str) or not 3 <= len(text.strip()) <= 500:
            raise ValueError("question attendue entre 3 et 500 caractères")
        cells = payload.get("cells", [])
        if not isinstance(cells, list) or len(cells) > 3:
            raise ValueError("cells attend au plus trois clés déclaratives")
        for key in cells:
            if not isinstance(key, str):
                raise ValueError("clé de cellule textuelle attendue")
            day, kind, column = key.split(":", 2)
            dt.date.fromisoformat(day)
            if not kind or ledger.column_base(column) not in {"RPE", "Dlr", "Plaisir"}:
                raise ValueError("cellule déclarative invalide")

    def _apply_question_review(self, connection: Connection, payload: dict, actor: str) -> None:
        question_id, answer_id = _uuid(payload["question_id"]), _uuid(payload["answer_id"])
        status, note = payload["status"], payload["note"]
        if status not in {"open", "deferred", "answered", "expired"}:
            raise ValueError("état de question inconnu")
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 500:
            raise ValueError("note de revue attendue entre 1 et 500 caractères")
        follow_up = payload.get("follow_up_on")
        if follow_up and not isinstance(follow_up, dt.date):
            follow_up = dt.date.fromisoformat(follow_up)
        if follow_up and status != "deferred":
            raise ValueError("une date de reprise exige deferred")
        question = connection.execute(sa.select(conversation_questions).where(
            conversation_questions.c.id == question_id).with_for_update()).mappings().one_or_none()
        answer = connection.execute(sa.select(conversation_messages).where(
            conversation_messages.c.id == answer_id)).mappings().one_or_none()
        if not question or not answer or answer["role"] != "athlete":
            raise ValueError("question ou message de l'athlète introuvable")
        if answer["psid"] != question["psid"] or answer["created_at"] < question["asked_at"]:
            raise ValueError("la réponse ne peut pas appartenir à cette question")
        connection.execute(conversation_questions.update().where(
            conversation_questions.c.id == question_id).values(
                status=status, review_note=note, follow_up_on=follow_up,
                candidate_answer_message_id=answer_id,
                resolved_at=(dt.datetime.now(dt.UTC) if status in {"answered", "expired"} else None)))
        _audit(connection, actor, "question.reviewed", "question", question_id,
               {"answer_id": str(answer_id), "status": status, "note": note,
                "follow_up_on": follow_up.isoformat() if follow_up else None})
    def _apply_thread(self, connection: Connection, payload: dict, actor: str) -> None:
        payload["opened_on"] = dt.date.fromisoformat(payload["opened_on"])
        if payload.get("follow_up_on"):
            payload["follow_up_on"] = dt.date.fromisoformat(payload["follow_up_on"])
        self.add_thread_in(connection, payload, actor=actor)

    def _apply_deadline(self, connection: Connection, payload: dict, actor: str) -> None:
        payload["due_on"] = dt.date.fromisoformat(payload["due_on"])
        for key in ("starts_on", "ends_on", "completed_on"):
            if payload.get(key) and isinstance(payload[key], str):
                payload[key] = dt.date.fromisoformat(payload[key])
        self.add_deadline_in(connection, payload, actor=actor)

    def _apply_session_start(self, connection: Connection, payload: dict,
                             actor: str) -> None:
        payload["declared_at"] = dt.datetime.fromisoformat(payload["declared_at"])
        self.declare_session_start_in(connection, payload, actor=actor)

    def _apply_session_match(self, connection: Connection, payload: dict,
                             actor: str) -> None:
        self.match_session_in(connection, payload["session_key"],
                              int(payload["strava_activity_id"]), actor=actor)

    def _apply_gear(self, connection: Connection, payload: dict, actor: str) -> None:
        self.upsert_gear_in(connection, payload, actor=actor)

    def _apply_prediction(self, connection: Connection, payload: dict, actor: str) -> None:
        if payload.get("due_on") and isinstance(payload["due_on"], str):
            payload["due_on"] = dt.date.fromisoformat(payload["due_on"])
        self.upsert_prediction_in(connection, payload, actor=actor)

    def _apply_permit_seal(self, connection: Connection, payload: dict, actor: str) -> None:
        self.seal_permit_choice_in(connection, payload, actor=actor)

    def _apply_correction(self, connection: Connection, payload: dict, actor: str) -> None:
        self.register_correction_in(connection, payload, actor=actor)

    def discard_turn(self, turn_id: str) -> int:
        with self.database.transaction() as connection:
            result = connection.execute(turn_commands.delete().where(
                turn_commands.c.turn_id == _uuid(turn_id),
                turn_commands.c.applied_at.is_(None)))
            return result.rowcount
