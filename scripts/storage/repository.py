"""Repositories transactionnels du domaine opérationnel."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from scripts.model import commands, ledger, local_time, session_cycle, session_kinds, weekly_plan
from scripts.model.activity import Activity

from .database import Database, StorageError
from .plan_policy import enforce_gate_resolution
from .schema import (
    MEASUREMENT_DEFINITIONS,
    agent_sessions,
    athlete_activity_states,
    athletes,
    audit_events,
    capability_evaluations,
    coach_predictions,
    conversation_messages,
    conversation_questions,
    conversation_threads,
    correction_deliveries,
    daily_checkins,
    deadlines,
    delivery_debts,
    gear_items,
    handoffs,
    inbound_activity_states,
    inbound_messages,
    messenger_message_refs,
    observation_metrics,
    observations,
    pain_readings,
    pain_zones,
    patience_notifications,
    permit_choices,
    plan_gates,
    plan_revisions,
    planned_session_sources,
    planned_sessions,
    proactive_delivery_budgets,
    proactive_jobs,
    proactive_markers,
    proactive_messages,
    projection_states,
    session_cycles,
    session_exposures,
    session_followups,
    session_predictions,
    strava_activity_intakes,
    strength_pain_readings,
    strength_report_claims,
    strength_report_daily_quotas,
    strength_results,
    strength_session_links,
    strength_sessions,
    strength_sets,
    turn_commands,
    turn_scopes,
    weekly_instruments,
    weekly_plans,
)

ATHLETE = "sam"
#: Une capture Strava sans ses flux ni sa météo : ce que lisent le débrief et le cycle
#: de séance. Les flux (plusieurs centaines de points) ne servent qu'au détail du carnet.
_INTAKE_CORE = tuple(column for column in strava_activity_intakes.c
                     if column.name not in {"streams", "weather"})

def _uuid(value: Any = None) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)) if value else uuid.uuid4()


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, uuid.UUID)):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return _plain(value)


def _audit(connection: Connection, actor: str, action: str,
           aggregate_type: str, aggregate_id: uuid.UUID, payload: dict) -> None:
    connection.execute(audit_events.insert().values(
        id=uuid.uuid4(), actor=actor, action=action,
        aggregate_type=aggregate_type, aggregate_id=aggregate_id, payload=payload))


def _request_projection(connection: Connection) -> None:
    """Un seul jeton durable : toutes les vues se régénèrent ensemble."""
    connection.execute(pg_insert(projection_states).values(
        singleton=True, dirty_since=sa.func.clock_timestamp(),
    ).on_conflict_do_update(index_elements=[projection_states.c.singleton], set_={
        # Renouveler le jeton empêche le projecteur d'acquitter une mutation
        # arrivée pendant sa génération.
        "dirty_since": sa.func.clock_timestamp(),
    }))


#: Une correction s'annonce dans la semaine où elle est consignée, ou jamais : l'audit du
#: 2026-10-09 a compté sept excuses livrées 23 à 26 jours après l'erreur, hors de tout
#: contexte. Passé ce délai, l'annonce expire ; la correction reste au journal.
CORRECTION_ANNOUNCE_WINDOW = dt.timedelta(days=7)

#: Les observations qui tranchent une prédiction d'elles-mêmes (2026-10-10). Le réveil
#: porte sa trace, par zone, dans `pain_readings` : 0 aucune, 0,5 très légère, 1 légère,
#: 2 nette.
RESOLVING_KINDS = (session_kinds.WAKE_TRACE,)
#: Motifs d'une FC moyenne douteuse (schéma `ck_observation_metrics_hr_doubt`).
HR_DOUBTS = ("sans_brassard", "contexte")
RESOLUTION_COLUMNS = ("resolves_kind", "resolves_on", "resolves_zone", "success_op",
                      "success_value")
_RULE_OPERATORS = {"<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
                   ">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
                   "=": lambda a, b: a == b}


def _resolution_rule(rule: dict | None, due_on: dt.date) -> dict:
    """`{kind, date, success_if: "<=1"}` → colonnes typées ; refuse au lieu d'interpréter."""
    if not rule:
        return dict.fromkeys(RESOLUTION_COLUMNS)
    kind = rule.get("kind")
    if kind not in RESOLVING_KINDS:
        raise StorageError(f"observation de solde inconnue : {kind!r} "
                           f"(attendu : {', '.join(RESOLVING_KINDS)})")
    day = rule.get("date")
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    if not isinstance(day, dt.date):
        raise StorageError("une prédiction rattachée nomme la date de son observation")
    if isinstance(due_on, str):
        due_on = dt.date.fromisoformat(due_on)
    if day > due_on:
        raise StorageError("l'observation qui tranche tombe après l'échéance de la prédiction")
    text = str(rule.get("success_if") or "").replace(" ", "")
    operator = next((op for op in ("<=", ">=", "<", ">", "=") if text.startswith(op)), None)
    try:
        threshold = float(text[len(operator):]) if operator else None
    except ValueError:
        threshold = None
    if threshold is None:
        raise StorageError(f"critère de réussite illisible : {rule.get('success_if')!r} "
                           "(forme attendue : <=1, <2, =0, >=1)")
    zone = rule.get("zone")
    if zone is not None and not isinstance(zone, str):
        raise StorageError("la zone d'une prédiction rattachée est un slug")
    return {"resolves_kind": kind, "resolves_on": day, "resolves_zone": zone,
            "success_op": operator, "success_value": threshold}


def _rule_holds(operator: str, threshold: float, value: float) -> bool:
    return _RULE_OPERATORS[operator](value, threshold)


class OperationalStore:
    """API unique des faits. Les appels publics ouvrent exactement une transaction."""

    def __init__(self, database: Database, *, athlete_id: str = ATHLETE) -> None:
        self.database = database
        self.athlete_id = athlete_id

    def ready(self, expected_revision: str | None = None) -> bool:
        return self.database.ready(expected_revision)

    def schema_revision(self) -> str | None:
        return self.database.schema_revision()

    # --------------------------------------------------------- unité de tour agent
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

    def review_question(self, payload: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self._apply_question_review(connection, payload, actor)

    def question_rows(self, limit: int = 20) -> list[dict]:
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(conversation_questions).where(
                conversation_questions.c.status.in_(("open", "deferred"))).order_by(
                    conversation_questions.c.asked_at.desc()).limit(limit)).mappings()]

    def conversation_search(self, psid: str, query: str, limit: int = 6) -> list[dict]:
        """Recherche des messages passés avec provenance et voisinage immédiat."""
        if not psid or not 3 <= len(query.strip()) <= 160 or not 1 <= limit <= 12:
            raise ValueError("psid, recherche de 3 à 160 caractères et limite 1 à 12 attendus")
        french: Any = sa.literal_column("'french'::regconfig")
        document = sa.func.to_tsvector(french, conversation_messages.c.body)
        search = sa.func.websearch_to_tsquery(french, query)
        rank = sa.func.ts_rank_cd(document, search)
        with self.database.engine.connect() as connection:
            hits = list(connection.execute(sa.select(
                conversation_messages.c.id, conversation_messages.c.role,
                conversation_messages.c.body, conversation_messages.c.created_at,
                rank.label("rank"),
            ).where(conversation_messages.c.psid == psid,
                    document.op("@@")(search)).order_by(
                        rank.desc(), conversation_messages.c.created_at.desc()).limit(limit)
            ).mappings())
            results = []
            for hit in hits:
                before = connection.execute(sa.select(
                    conversation_messages.c.role, conversation_messages.c.body,
                    conversation_messages.c.created_at,
                ).where(conversation_messages.c.psid == psid,
                        conversation_messages.c.created_at < hit["created_at"]
                ).order_by(conversation_messages.c.created_at.desc()).limit(1)).mappings().first()
                after = connection.execute(sa.select(
                    conversation_messages.c.role, conversation_messages.c.body,
                    conversation_messages.c.created_at,
                ).where(conversation_messages.c.psid == psid,
                        conversation_messages.c.created_at > hit["created_at"]
                ).order_by(conversation_messages.c.created_at).limit(1)).mappings().first()
                results.append({"hit": dict(hit), "before": dict(before) if before else None,
                                "after": dict(after) if after else None})
            return results

    def conversation_search_for_turn(self, turn_id: str, query: str,
                                     limit: int = 6) -> list[dict]:
        """Le seul périmètre historique exposé à la CLI de l'agent."""
        try:
            identifier = uuid.UUID(turn_id)
        except (ValueError, TypeError) as exc:
            raise ValueError("identifiant de tour invalide") from exc
        with self.database.engine.connect() as connection:
            psid = connection.execute(sa.select(turn_scopes.c.psid).where(
                turn_scopes.c.turn_id == identifier,
                turn_scopes.c.created_at >= dt.datetime.now(dt.UTC) - dt.timedelta(hours=4),
            )).scalar_one_or_none()
        if psid is None:
            raise ValueError("tour absent ou expiré")
        return self.conversation_search(psid, query, limit)

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

    # ------------------------------------------------------------- observations
    def add_observation(self, payload: dict, *, actor: str = "application") -> uuid.UUID:
        with self.database.transaction() as connection:
            return self.add_observation_in(connection, payload, actor=actor)

    def add_observation_in(self, connection: Connection, payload: dict,
                           *, actor: str = "application") -> uuid.UUID:
        observation_id = _uuid(payload.get("id"))
        strava_id = payload.get("strava_activity_id")
        values = {
            "id": observation_id,
            "athlete_id": payload.get("athlete_id", self.athlete_id),
            "occurred_on": payload["occurred_on"],
            "started_at": payload.get("started_at"),
            "kind": payload["kind"],
            "routine": payload.get("routine"),
            "idempotency_key": payload["idempotency_key"],
            "strava_activity_id": int(strava_id) if strava_id else None,
            "gear_id": payload.get("gear_id"),
            "note": payload.get("note", ""),
            "source": payload.get("source", actor),
            "supersedes_id": payload.get("supersedes_id"),
        }
        statement: Any = pg_insert(observations).values(**values).on_conflict_do_nothing(
            constraint="uq_observations_idempotency").returning(observations.c.id)
        inserted = connection.execute(statement).scalar_one_or_none()
        if inserted is None:
            existing = connection.execute(sa.select(observations).where(
                observations.c.athlete_id == values["athlete_id"],
                observations.c.idempotency_key == values["idempotency_key"],
            )).mappings().one()
            stored_metrics = connection.execute(sa.select(observation_metrics).where(
                observation_metrics.c.observation_id == existing["id"]
            )).mappings().one()
            expected = self._metric_values(payload.get("measurements", {}))
            comparable = ("occurred_on", "started_at", "kind", "routine", "strava_activity_id",
                          "gear_id", "note", "supersedes_id")
            stored_pains = self._pains_of_in(connection, existing["id"])
            given_pains = {zone: reading for zone, reading in self._pain_values_in(
                connection, values["kind"], payload.get("pains", {})).items()
                if zone in payload.get("pains", {})}
            if any(existing[key] != values[key] for key in comparable) or any(
                    _plain(stored_metrics[key]) != _plain(expected[key]) for key in expected
            ) or any(stored_pains.get(zone) != {"state": reading["state"],
                                                 "value": _plain(reading["value"])}
                     for zone, reading in given_pains.items()):
                raise StorageError(
                    f"collision d'idempotence pour l'observation {values['idempotency_key']!r}")
            return existing["id"]
        observation_id = inserted

        connection.execute(observation_metrics.insert().values(
            observation_id=observation_id,
            **self._metric_values(payload.get("measurements", {})),
        ))
        pains = self._pain_values_in(connection, values["kind"], payload.get("pains", {}))
        for zone, reading in pains.items():
            connection.execute(pain_readings.insert().values(
                observation_id=observation_id, zone=zone, athlete_id=values["athlete_id"],
                **reading))
        event = {key: _plain(value) for key, value in values.items()}
        _audit(connection, actor, "observation.created", "observation", observation_id, event)
        if values["kind"] in RESOLVING_KINDS:
            for zone, reading in pains.items():
                if reading["state"] in ("known", "unavailable"):
                    self.settle_observed_predictions_in(
                        connection, values["kind"], values["occurred_on"], zone,
                        float(reading["value"]) if reading["state"] == "known" else None,
                        actor=actor)
        if values["strava_activity_id"] and values["started_at"]:
            self._auto_match_observation_in(
                connection, observation_id, payload.get("session_key"), actor=actor)
            connection.execute(strava_activity_intakes.update().where(
                strava_activity_intakes.c.activity_id == values["strava_activity_id"]
            ).values(status="matched", observation_id=observation_id, error="",
                     updated_at=sa.func.now()))
        _request_projection(connection)
        return observation_id

    @staticmethod
    def _metric_values(measurements: dict) -> dict:
        unknown = sorted(set(measurements) - set(MEASUREMENT_DEFINITIONS))
        if unknown:
            raise StorageError("mesure inconnue : " + ", ".join(unknown))
        values = {}
        for metric in MEASUREMENT_DEFINITIONS:
            measurement = measurements.get(metric, {"state": "not_applicable"})
            state = measurement.get("state", "known")
            values[f"{metric}_state"] = state
            values[metric] = measurement.get("value") if state == "known" else None
        return values

    # --------------------------------------------------------- zones de douleur
    def sync_pain_zones(self, zones: list[tuple[str, str]], *,
                        actor: str = "application") -> None:
        """Aligne la référence sur `[[injuries]]` : une zone retirée passe inactive.

        Jamais supprimée : ses lectures passées restent rattachées et lisibles.
        """
        with self.database.transaction() as connection:
            declared = {slug for slug, _ in zones}
            for slug, label in zones:
                connection.execute(pg_insert(pain_zones).values(
                    athlete_id=self.athlete_id, slug=slug, label=label, active=True,
                ).on_conflict_do_update(
                    index_elements=[pain_zones.c.athlete_id, pain_zones.c.slug],
                    set_={"label": label, "active": True}))
            connection.execute(pain_zones.update().where(
                pain_zones.c.athlete_id == self.athlete_id,
                pain_zones.c.slug.not_in(declared) if declared else sa.true(),
            ).values(active=False))
            _audit(connection, actor, "pain_zones.synced", "athlete",
                   uuid.uuid5(uuid.NAMESPACE_URL, f"athlete:{self.athlete_id}"),
                   {"zones": sorted(declared)})

    def pain_zones(self) -> list[dict]:
        """Les zones surveillées, actives d'abord, dans l'ordre de leur déclaration."""
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(
                pain_zones.c.slug, pain_zones.c.label, pain_zones.c.active,
            ).where(pain_zones.c.athlete_id == self.athlete_id).order_by(
                pain_zones.c.active.desc(), pain_zones.c.created_at,
                pain_zones.c.slug)).mappings()]

    def _active_zones_in(self, connection: Connection) -> list[str]:
        return list(connection.execute(sa.select(pain_zones.c.slug).where(
            pain_zones.c.athlete_id == self.athlete_id, pain_zones.c.active,
        ).order_by(pain_zones.c.created_at, pain_zones.c.slug)).scalars())

    def _pain_values_in(self, connection: Connection, kind: str,
                        given: dict) -> dict[str, dict[str, Any]]:
        """Une lecture par zone active : la valeur donnée, sinon `pending` si le type
        d'observation exige la douleur, `not_applicable` sinon. Une zone inconnue ou
        retirée est refusée plutôt que rangée dans une autre."""
        active = self._active_zones_in(connection)
        unknown = sorted(set(given) - set(active))
        if unknown:
            raise StorageError("zone de douleur inconnue ou inactive : " + ", ".join(unknown)
                               + (f" (actives : {', '.join(active)})" if active
                                  else " (aucune zone surveillée)"))
        default = "pending" if ledger.requires_pain(kind) else "not_applicable"
        readings = {}
        for zone in active:
            reading = given.get(zone, {"state": default})
            state = reading.get("state", "known")
            readings[zone] = {"state": state,
                              "value": reading.get("value") if state == "known" else None}
        return readings

    @staticmethod
    def _pains_of_in(connection: Connection, observation_id: uuid.UUID) -> dict[str, dict]:
        return {row["zone"]: {"state": row["state"], "value": _plain(row["value"])}
                for row in connection.execute(sa.select(
                    pain_readings.c.zone, pain_readings.c.state, pain_readings.c.value,
                ).where(pain_readings.c.observation_id == observation_id)).mappings()}

    def complete_measurement(self, observation_id: uuid.UUID, metric: str,
                             value: float | None, *, zone: str | None = None,
                             unavailable: bool = False,
                             actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.complete_measurement_in(connection, observation_id, metric, value,
                                         zone=zone, unavailable=unavailable, actor=actor)

    def doubt_hr(self, observation_id: uuid.UUID, reason: str, note: str,
                 *, actor: str = "application") -> None:
        """Déclare douteuse une FC moyenne connue, une seule fois, avec son motif.

        La mesure reste immuable : seule l'annotation dit ce qu'elle vaut. Le motif
        textuel (les mots de l'athlète) part dans le journal d'audit.
        """
        with self.database.transaction() as connection:
            self.doubt_hr_in(connection, observation_id, reason, note, actor=actor)

    def doubt_hr_in(self, connection: Connection, observation_id: uuid.UUID, reason: str,
                    note: str, *, actor: str = "application") -> None:
        if reason not in HR_DOUBTS:
            raise StorageError(f"motif de FC douteuse inconnu : {reason}")
        if not note.strip():
            raise StorageError("une FC douteuse porte la cause déclarée")
        result = connection.execute(observation_metrics.update().where(
            observation_metrics.c.observation_id == observation_id,
            observation_metrics.c.hr_avg_bpm_state == "known",
            observation_metrics.c.hr_doubt.is_(None),
        ).values(hr_doubt=reason))
        if result.rowcount != 1:
            raise StorageError("FC absente, inconnue ou déjà déclarée douteuse")
        _audit(connection, actor, "measurement.hr_doubted", "observation",
               observation_id, {"reason": reason, "note": note.strip()})
        _request_projection(connection)

    def complete_measurement_in(self, connection: Connection, observation_id: uuid.UUID,
                                metric: str, value: float | None, *, zone: str | None = None,
                                unavailable: bool = False,
                                actor: str = "application") -> None:
        if metric == "pain":
            self._complete_pain_in(connection, observation_id, zone, value,
                                   unavailable=unavailable, actor=actor)
            return
        if zone is not None:
            raise StorageError(f"seule la douleur se mesure par zone, pas {metric}")
        if metric not in MEASUREMENT_DEFINITIONS:
            raise StorageError(f"mesure inconnue : {metric}")
        state = "unavailable" if unavailable else "known"
        state_column = observation_metrics.c[f"{metric}_state"]
        result = connection.execute(observation_metrics.update().where(
            observation_metrics.c.observation_id == observation_id,
            # ⭐ SEUL `known` est immuable. `not_applicable` a longtemps été bloqué avec
            # lui, alors que ce n'est pas une mesure : c'est le DÉFAUT de toute colonne
            # absente à la création (`_metric_values`). Une séance consignée sans ses
            # chiffres devenait donc définitivement incomplétable — le vélo du
            # 2026-08-13, consigné à la main après un débrief aveugle, a pesé zéro dans
            # la jauge de W33 pendant trois jours, ses 1 h 43 et ses 647 m de D+
            # irrattrapables. Un défaut d'absence n'est pas une donnée : il se remplit.
            state_column.in_(("pending", "unavailable", "not_applicable")),
        ).values({state_column: state,
                  observation_metrics.c[metric]: None if unavailable else value}))
        if result.rowcount != 1:
            raise StorageError("mesure absente, déjà connue ou ambiguë")
        _audit(connection, actor, "measurement.completed", "observation", observation_id,
               {"metric": metric, "state": state, "value": value})
        _request_projection(connection)

    def _complete_pain_in(self, connection: Connection, observation_id: uuid.UUID,
                          zone: str | None, value: float | None, *, unavailable: bool,
                          actor: str) -> None:
        """Comme toute mesure : seule une lecture `known` est immuable."""
        zones = list(connection.execute(sa.select(pain_readings.c.zone).where(
            pain_readings.c.observation_id == observation_id)).scalars())
        if zone is None:
            if len(zones) != 1:
                raise StorageError("douleur : nomme la zone (" + (", ".join(sorted(zones))
                                   or "aucune zone sur cette observation") + ")")
            zone = zones[0]
        state = "unavailable" if unavailable else "known"
        result = connection.execute(pain_readings.update().where(
            pain_readings.c.observation_id == observation_id,
            pain_readings.c.zone == zone,
            pain_readings.c.state.in_(("pending", "unavailable", "not_applicable")),
        ).values(state=state, value=None if unavailable else value,
                 recorded_at=sa.func.now()))
        if result.rowcount != 1:
            raise StorageError(f"douleur {zone} absente, déjà connue ou ambiguë")
        _audit(connection, actor, "measurement.completed", "observation", observation_id,
               {"metric": "pain", "zone": zone, "state": state, "value": value})
        kind, day = connection.execute(sa.select(
            observations.c.kind, observations.c.occurred_on).where(
                observations.c.id == observation_id)).one()
        if kind in RESOLVING_KINDS:
            self.settle_observed_predictions_in(
                connection, kind, day, zone,
                None if unavailable or value is None else float(value), actor=actor)
        _request_projection(connection)

    def link_activity(self, idempotency_key: str, strava_activity_id: int, *,
                      actor: str = "application") -> uuid.UUID:
        """Rattache une observation existante à son identité Strava exacte.

        Une observation née dans Messenger peut précéder l'arrivée de l'activité chez
        Strava. Le fait ne se duplique pas : son identité externe s'ajoute à la ligne
        existante. Rejouer le même rattachement est idempotent ; viser une autre activité
        est un conflit explicite.
        """
        with self.database.transaction() as connection:
            return self.link_activity_in(
                connection, idempotency_key, strava_activity_id, actor=actor)

    def link_activity_in(self, connection: Connection, idempotency_key: str,
                         strava_activity_id: int, *, actor: str) -> uuid.UUID:
        row = connection.execute(sa.select(
            observations.c.id, observations.c.strava_activity_id,
        ).where(
            observations.c.athlete_id == self.athlete_id,
            observations.c.idempotency_key == idempotency_key,
        ).with_for_update()).one_or_none()
        if row is None:
            raise StorageError(f"observation inconnue : {idempotency_key}")
        observation_id, current = row
        wanted = int(strava_activity_id)
        if current == wanted:
            return observation_id
        if current is not None:
            raise StorageError(
                f"observation {idempotency_key} déjà rattachée à l'activité {current}")
        connection.execute(observations.update().where(
            observations.c.id == observation_id,
        ).values(strava_activity_id=wanted))
        _audit(connection, actor, "activity.linked", "observation", observation_id,
               {"idempotency_key": idempotency_key, "strava_activity_id": wanted})
        _request_projection(connection)
        return observation_id

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
            "hr_avg_bpm": ("FCmoy", activity.hr_avg_bpm),
            "hr_max_bpm": ("FCmax", activity.hr_max_bpm),
            "cadence_spm": ("Cad", activity.cadence_spm or activity.cadence_rpm),
            "elevation_gain_m": ("D+", activity.elevation_gain_m),
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
        """Capture les faits objectifs avant tout raisonnement de coaching.

        Une activité n'est convertie en observation typée que si le plan offre un
        candidat unique le même jour. Sinon le distillat reste durable et l'ambiguïté
        est exposée ; aucune taxonomie sportive n'est inventée à partir d'un titre.
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
        """Persiste un enrichissement et le projette sur l'observation, si elle existe.

        L'intake garde le résultat même quand le rapprochement est encore ambigu. Un
        appariement ultérieur récupère donc D- et WBGT sans rappeler les fournisseurs.
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
                    # Le coach a pu consigner la séance avant le webhook : sa mesure
                    # reste (une mesure connue est immuable) et l'écart n'est qu'une
                    # trace. Le 2026-09-26, lever ici annulait la capture entière et
                    # le débrief expirait après deux heures de reprises.
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
        """La capture Strava d'une observation, avec ses flux et sa météo s'ils sont relevés."""
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(strava_activity_intakes).where(
                strava_activity_intakes.c.observation_id == observation_id,
                strava_activity_intakes.c.athlete_id == self.athlete_id,
            )).mappings().one_or_none()
        return None if row is None else {key: _plain(value) for key, value in dict(row).items()}

    def store_strava_detail(self, activity_id: int, *, streams: dict | None = None,
                            weather: dict | None = None,
                            actor: str = "carnet") -> None:
        """Garde les flux allégés et la météo relevés pour le détail de séance."""
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
        """RPE, plaisir et douleur par zone connus d'une observation, sans l'attente."""
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

    # ----------------------------------------------------------- cycle de séance
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
        """Le cycle de chaque séance d'une semaine et l'observation qui l'a soldée.

        Lecture du carnet : il met le fait (l'observation) en regard de l'intention
        (la séance). Rien n'est recopié ; l'observation se relit par son identifiant
        dans `observation_rows`.
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
        """Vue de diagnostic pour le coach : attentes et ambiguïtés."""
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

    def activity_recorded(self, strava_activity_id: int) -> bool:
        """Cette identité Strava exacte a-t-elle atterri au registre ?

        ⭐ LA CONTREPARTIE DE `required_weeks`. Un tour prescriptif doit prouver qu'il a
        révisé la semaine ; rien n'exigeait qu'un débrief prouve qu'il a consigné la
        séance dont il parlait. Le 13/08 le coach a répondu 18 s après un `exec` que le
        bac à sable a tué (« bwrap: Failed RTM_NEWADDR »), sans rien lire ni rien
        écrire, et le service a journalisé un succès. Le 14/08 il a rendu SILENCE parce
        que `strava.py` n'avait pas ses identifiants. Deux sorties jamais consignées,
        découvertes trois jours plus tard par un volume hebdomadaire faux.

        Ce contrôle ne prouve jamais l'absence du fait : une observation Messenger peut
        exister sans avoir encore été rattachée. Il prouve uniquement que le débrief a
        écrit ou réconcilié l'identité Strava qu'il avait reçue.
        """
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(sa.func.count()).select_from(observations).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.strava_activity_id == int(strava_activity_id),
            )).scalar_one() > 0

    def observation_id(self, idempotency_key: str) -> uuid.UUID | None:
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(observations.c.id).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.idempotency_key == idempotency_key,
            )).scalar_one_or_none()

    def observation_rows(self) -> list[dict]:
        query = sa.select(observations, observation_metrics).join(
            observation_metrics,
            observation_metrics.c.observation_id == observations.c.id,
        ).where(
            observations.c.athlete_id == self.athlete_id,
        ).order_by(observations.c.occurred_on.desc(), observations.c.created_at.desc())
        with self.database.engine.connect() as connection:
            result = []
            for row in connection.execute(query).mappings():
                entry = {
                    "id": row["id"],
                    "occurred_on": row["occurred_on"],
                    "started_at": row["started_at"],
                    "kind": row["kind"],
                    "routine": row["routine"],
                    "idempotency_key": row["idempotency_key"],
                    "strava_activity_id": row["strava_activity_id"],
                    "gear_id": row["gear_id"],
                    "note": row["note"],
                    "source": row["source"],
                    "hr_doubt": row["hr_doubt"],
                    "measurements": {},
                }
                for metric in MEASUREMENT_DEFINITIONS:
                    entry["measurements"][metric] = {
                        "state": row[f"{metric}_state"], "value": _plain(row[metric])}
                entry["pains"] = {}
                result.append(entry)
            by_id = {entry["id"]: entry for entry in result}
            for reading in connection.execute(sa.select(
                    pain_readings.c.observation_id, pain_readings.c.zone,
                    pain_readings.c.state, pain_readings.c.value,
            ).where(pain_readings.c.athlete_id == self.athlete_id).order_by(
                    pain_readings.c.zone)).mappings():
                owner = by_id.get(reading["observation_id"])
                if owner is not None:
                    owner["pains"][reading["zone"]] = {
                        "state": reading["state"], "value": _plain(reading["value"])}
            return result

    # -------------------------------------------------------------- expositions
    def upsert_exposure(self, payload: dict, *, actor: str = "application") -> uuid.UUID:
        with self.database.transaction() as connection:
            return self.upsert_exposure_in(connection, payload, actor=actor)

    def upsert_exposure_in(self, connection: Connection, payload: dict,
                           *, actor: str = "application") -> uuid.UUID:
        key = str(payload["exposure_key"]).strip()
        observation_id = connection.execute(sa.select(observations.c.id).where(
            observations.c.athlete_id == self.athlete_id,
            observations.c.idempotency_key == payload["observation_key"],
        )).scalar_one_or_none()
        if observation_id is None:
            raise StorageError(f"observation inconnue : {payload['observation_key']}")
        existing = connection.execute(sa.select(session_exposures).where(
            session_exposures.c.athlete_id == self.athlete_id,
            session_exposures.c.exposure_key == key,
        ).with_for_update()).mappings().one_or_none()
        values = {
            "observation_id": observation_id,
            "exposure_type": payload["exposure_type"],
            "origin": payload["origin"],
            "status": payload["status"],
            "duration_min": payload.get("duration_min"),
            "target_value": payload.get("target_value"),
            "target_unit": payload.get("target_unit"),
            "tolerance": payload.get("tolerance", "unknown"),
            "note": payload.get("note", ""),
        }
        if existing is not None:
            comparable = {name: _plain(existing[name]) for name in values}
            incoming = {name: _plain(value) for name, value in values.items()}
            if comparable == incoming:
                return existing["id"]
            # L'intention peut être précisée ou corrigée plus tard, comme sa dose ou
            # sa tolérance. Même clé, même exposition ; l'audit conserve la trace.
            exposure_id = existing["id"]
            connection.execute(session_exposures.update().where(
                session_exposures.c.id == exposure_id).values(
                    **values, updated_at=sa.func.now()))
        else:
            exposure_id = _uuid(payload.get("id"))
            connection.execute(session_exposures.insert().values(
                id=exposure_id, athlete_id=self.athlete_id, exposure_key=key, **values))
        _audit(connection, actor, "exposure.upsert", "session_exposure", exposure_id,
               {"exposure_key": key, **_jsonable(values)})
        _request_projection(connection)
        return exposure_id

    def exposure_rows(self, *, confirmed_only: bool = False) -> list[dict]:
        query = sa.select(
            session_exposures,
            observations.c.idempotency_key.label("observation_key"),
            observations.c.occurred_on,
        ).join(observations, observations.c.id == session_exposures.c.observation_id).where(
            session_exposures.c.athlete_id == self.athlete_id)
        if confirmed_only:
            query = query.where(session_exposures.c.status == "confirmed")
        query = query.order_by(observations.c.occurred_on.desc(),
                               session_exposures.c.created_at.desc())
        with self.database.engine.connect() as connection:
            return [{name: _plain(value) for name, value in dict(row).items()}
                    for row in connection.execute(query).mappings()]

    # --------------------------------------------------------------- quotidien
    def put_daily(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.put_daily_in(connection, row, actor=actor)

    def put_daily_in(self, connection: Connection, row: dict,
                     *, actor: str = "application") -> None:
        values = {
            "athlete_id": row.get("athlete_id", self.athlete_id),
            "checked_on": row.get("checked_on", row.get("day")), "sleep": row.get("sleep"),
            "life_load": row.get("life_load"), "desire": row.get("desire"),
            "note": row.get("note", ""),
        }
        statement = pg_insert(daily_checkins).values(**values).on_conflict_do_update(
            index_elements=[daily_checkins.c.athlete_id, daily_checkins.c.checked_on],
            set_={key: value for key, value in values.items()
                  if key not in ("athlete_id", "checked_on")},
        )
        aggregate_id = uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"daily:{values['athlete_id']}:{values['checked_on']}")
        connection.execute(statement)
        _audit(connection, actor, "daily.upserted", "daily", aggregate_id,
               {key: _plain(value) for key, value in values.items()})
        _request_projection(connection)

    def daily_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(daily_checkins).where(
                daily_checkins.c.athlete_id == self.athlete_id,
            ).order_by(daily_checkins.c.checked_on)).mappings()
            return [dict(row) for row in rows]

    def weekly_instrument_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(weekly_instruments).where(
                weekly_instruments.c.athlete_id == self.athlete_id,
            ).order_by(weekly_instruments.c.iso_week)).mappings()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------- renfo
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

    # --------------------------------------------------------- programme hebdo
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

    # ---------------------------------------------------- capacités / fils / échéances
    def evaluate_capability(self, row: dict, *, actor: str = "application") -> bool:
        with self.database.transaction() as connection:
            return self.add_capability_evaluation_in(connection, row, actor=actor)

    def add_capability_evaluation_in(self, connection: Connection, row: dict,
                                     *, actor: str = "application") -> bool:
        """Une évaluation par capacité et par jour, immuable une fois écrite.

        Renvoie False quand la même évaluation existait déjà (rejeu sans effet). Un
        contenu différent pour le même jour est refusé : le 2026-10-13, un
        `--next-test` corrigé pour le 2026-10-11 a été ignoré par le conflit alors que
        la CLI annonçait « enregistré ».
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
                          if row["status"] == "clos" else None)}
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
        """Rafraîchit le compteur fournisseur sans écraser le rôle personnalisé.

        Une paire inconnue entre avec un rôle explicitement à qualifier et un seuil
        d'inspection conservateur. Le premier réglage humain de ces deux champs reste
        ensuite propriétaire : Strava connaît les kilomètres, pas l'usage prévu.
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
            # Une prédiction soldée alors que sa condition n'a pas été tenue, ou sans
            # l'observation qui devait la trancher, n'a pas été testée : elle passe en
            # `void`, motif à l'appui. Jamais l'inverse, et jamais vrai ↔ faux : un score
            # qu'on retourne après coup n'est plus une calibration (audit du 2026-10-09).
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
        """La zone que la prédiction attend : nommée, ou la seule zone surveillée."""
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
        """La dernière valeur connue, dans cette zone, de l'observation qui tranche."""
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
        """Solde les prédictions ouvertes qui attendaient cette observation, dans sa zone.

        `value=None` : l'observation est déclarée indisponible ou n'est jamais venue ;
        la prédiction n'a pas été testée, elle passe en `void`, jamais en faux.
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
        """Ferme les prédictions dont la date d'observation est passée.

        L'observation venue sans déclencher le solde (écrite avant le rattachement)
        tranche encore ; son absence rend la prédiction `void`.
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
        """Scelle le choix du coach et sa prédiction dans la même transaction.

        Le choix est immuable : la même écriture rejouée ne fait rien, une écriture qui
        le changerait est refusée. La prédiction liée est ce qui le rend falsifiable.
        """
        scheduled_on = row["scheduled_on"]
        if isinstance(scheduled_on, str):
            scheduled_on = dt.date.fromisoformat(scheduled_on)
        values = {"athlete_id": self.athlete_id, "domain": "ef",
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
        """Les corrections, avec le nombre de récidives et la date de la première.

        Une récidive remplace le résumé sous la même clé ; sans ce compte, la ligne ne
        disait plus qu'une erreur était revenue (audit du 2026-10-09). Le journal
        d'audit garde chaque version : il suffit de le compter.
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

    def add_proactive_message_in(self, connection: Connection, row: dict,
                                 *, actor: str = "application") -> uuid.UUID:
        message_id = _uuid(row.get("id"))
        values = {
            "id": message_id, "athlete_id": self.athlete_id,
            "source_key": row["source_key"], "sent_at": row["sent_at"],
            "kind": row["kind"], "angle": row.get("angle"),
            "summary": row["summary"],
        }
        inserted = connection.execute(pg_insert(proactive_messages).values(
            **values).on_conflict_do_nothing(
                constraint="uq_proactive_messages_source_key"
            ).returning(proactive_messages.c.id)).scalar_one_or_none()
        if inserted is not None:
            _audit(connection, actor, "proactive_message.added", "proactive_message",
                   inserted, _jsonable(values))
            return inserted
        existing = connection.execute(sa.select(proactive_messages).where(
            proactive_messages.c.athlete_id == self.athlete_id,
            proactive_messages.c.source_key == row["source_key"],
        )).mappings().one()
        comparable = ("sent_at", "kind", "angle", "summary")
        if any(existing[key] != values[key] for key in comparable):
            raise StorageError(
                f"collision d'idempotence pour le message {row['source_key']!r}")
        return existing["id"]

    def add_proactive_message(self, row: dict, *, actor: str = "application") -> uuid.UUID:
        with self.database.transaction() as connection:
            return self.add_proactive_message_in(connection, row, actor=actor)

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

    def proactive_message_rows(self, *, limit: int = 10) -> list[dict]:
        if limit < 1:
            raise ValueError("limit doit être positif")
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(
                proactive_messages).where(
                    proactive_messages.c.athlete_id == self.athlete_id
                ).order_by(proactive_messages.c.sent_at.desc()).limit(limit)).mappings()]

    def counts(self) -> dict[str, int]:
        tables = {
            "athletes": athletes, "observations": observations,
            "observation_metrics": observation_metrics,
            "session_exposures": session_exposures,
            "daily_checkins": daily_checkins, "weekly_instruments": weekly_instruments,
            "strength_sessions": strength_sessions,
            "strength_results": strength_results, "strength_sets": strength_sets,
            "weekly_plans": weekly_plans, "plan_revisions": plan_revisions,
            "planned_sessions": planned_sessions, "plan_gates": plan_gates,
            "session_cycles": session_cycles,
            "session_predictions": session_predictions,
            "session_followups": session_followups,
            "capability_evaluations": capability_evaluations,
            "conversation_threads": conversation_threads, "deadlines": deadlines,
            "conversation_messages": conversation_messages,
            "conversation_questions": conversation_questions,
            "gear_items": gear_items, "coach_predictions": coach_predictions,
            "correction_deliveries": correction_deliveries,
            "agent_sessions": agent_sessions, "handoffs": handoffs,
            "inbound_activity_states": inbound_activity_states,
            "inbound_messages": inbound_messages,
            "messenger_message_refs": messenger_message_refs,
            "patience_notifications": patience_notifications,
            "athlete_activity_states": athlete_activity_states,
            "strength_report_daily_quotas": strength_report_daily_quotas,
            "proactive_delivery_budgets": proactive_delivery_budgets,
            "proactive_jobs": proactive_jobs, "proactive_markers": proactive_markers,
            "proactive_messages": proactive_messages,
            "strength_report_claims": strength_report_claims,
            "strength_session_links": strength_session_links,
            "delivery_debts": delivery_debts,
            "turn_commands": turn_commands,
        }
        with self.database.engine.connect() as connection:
            return {name: connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                    for name, table in tables.items()}

    #: Ce qui EXPIRE, et au bout de combien de jours. Rien qui porte un FAIT : les
    #: marqueurs sont des traces de mécanique consultées le jour même et jamais après.
    #:
    #: ⭐ Une seule table était purgée (`proactive_jobs`, cent dernières). Les autres
    #: grossissaient sans fin : un marqueur par incident signalé et par cellule
    #: réclamée, emporté dans chaque sauvegarde chiffrée bi-horaire.
    #:
    #: `turn_commands` N'EXPIRE PAS : `command_key` est la barrière d'idempotence. La
    #: supprimer après 30 jours permettait de remettre une vieille commande en attente,
    #: même quand une contrainte métier empêchait ensuite son second effet.
    #:
    #: Le journal d'audit n'expire pas : l'application ne peut ni le modifier ni
    #: l'effacer. `projection_states` n'est pas un journal, seulement un bit durable.
    EXPIRY_DAYS: ClassVar[dict[str, int]] = {
        "proactive_markers": 90,   # « déjà signalé aujourd'hui », « déjà réclamé »
        "inbound_messages": 90,    # bien au-delà de la fenêtre de rejeu de Meta
        "strength_session_links": 30,  # délai après expiration, pour diagnostic
    }

    def purge_expired(self, now: dt.datetime | None = None) -> dict[str, int]:
        """Efface les traces de mécanique périmées. Rend ce qui a été supprimé.

        Appelée au démarrage, avant qu'un tour ne soit en vol.
        """
        moment = now or dt.datetime.now(dt.UTC)
        removed: dict[str, int] = {}
        with self.database.transaction() as connection:
            for table, column, days, guards in (
                (proactive_markers, proactive_markers.c.created_at,
                 self.EXPIRY_DAYS["proactive_markers"], ()),
                (inbound_messages, inbound_messages.c.received_at,
                 self.EXPIRY_DAYS["inbound_messages"],
                 (inbound_messages.c.status == "done",)),
                (strength_session_links, strength_session_links.c.expires_at,
                 self.EXPIRY_DAYS["strength_session_links"], ()),
            ):
                count = connection.execute(table.delete().where(
                    column < moment - dt.timedelta(days=days), *guards)).rowcount
                if count:
                    removed[table.name] = count
        return removed

    def projection_pending(self) -> bool:
        return self.projection_token() is not None

    def projection_token(self) -> dt.datetime | None:
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(projection_states.c.dirty_since).where(
                projection_states.c.singleton.is_(True))).scalar_one_or_none()

    def mark_projected(self, token: dt.datetime | None) -> bool:
        if token is None:
            return False
        with self.database.transaction() as connection:
            result = connection.execute(projection_states.update().where(
                projection_states.c.singleton.is_(True),
                projection_states.c.dirty_since == token,
            ).values(dirty_since=None, last_projected_at=sa.func.now()))
            return result.rowcount == 1
