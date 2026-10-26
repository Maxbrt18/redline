"""État durable du service : sessions, files, claims et quotas atomiques."""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import RowMapping
from sqlalchemy.dialects.postgresql import insert as pg_insert

from scripts import signals as signal_model
from scripts.model import commands, session_cycle
from scripts.storage.database import Database
from scripts.storage.repository import ATHLETE, CORRECTION_ANNOUNCE_WINDOW, OperationalStore
from scripts.storage.schema import (
    agent_sessions,
    athlete_activity_states,
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
    outbound_deliveries,
    patience_notifications,
    planned_sessions,
    proactive_delivery_budgets,
    proactive_jobs,
    proactive_markers,
    session_cycles,
    strength_session_links,
    strength_sessions,
    turn_commands,
    turn_scopes,
    weekly_plans,
)

from . import strength
from .attachments import safe_conversation_label
from .config import Config
from .race_countdown import compile_deadline
from .relationship import SERVICE_LINE_PREFIXES, question_fingerprint, render_memory
from .state import ProactiveState, StateStore

JOB_KEEP = 100
_RELATION_STOPWORDS = {
    "alors", "après", "avec", "cette", "comme", "dans", "depuis", "elle", "elles",
    "encore", "entre", "faire", "mais", "même", "pour", "plus", "sans", "sera",
    "sont", "tout", "toute", "très", "vers", "votre",
}


def _at(epoch: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(epoch, tz=dt.UTC)


def _epoch(value: dt.datetime | None) -> float:
    return value.timestamp() if value else 0.0


class PostgresStateStore(StateStore):
    def __init__(self, database: Database) -> None:
        self.database = database
        self.proactive = PostgresProactiveState(self)

    def register_turn(self, turn_id: str, psid: str) -> None:
        """Lie la lecture historique à ce tour sans exposer le PSID au shell."""
        with self.database.transaction() as connection:
            connection.execute(turn_scopes.delete().where(
                turn_scopes.c.created_at < dt.datetime.now(dt.UTC) - dt.timedelta(days=1)))
            connection.execute(turn_scopes.insert().values(turn_id=uuid.UUID(turn_id), psid=psid))

    def session_id(self, psid: str, provider: str) -> str | None:
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(agent_sessions.c.session_id).where(
                agent_sessions.c.psid == psid, agent_sessions.c.provider == provider,
            )).scalar_one_or_none()

    def session_age(self, psid: str, provider: str) -> float:
        with self.database.engine.connect() as connection:
            value = connection.execute(sa.select(agent_sessions.c.last_seen_at).where(
                agent_sessions.c.psid == psid, agent_sessions.c.provider == provider,
            )).scalar_one_or_none()
        return time.time() - _epoch(value) if value else float("inf")

    def session_prompt(self, psid: str, provider: str) -> str:
        """L'empreinte des instructions sous lesquelles cette session a été ouverte."""
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(agent_sessions.c.prompt_digest).where(
                agent_sessions.c.psid == psid, agent_sessions.c.provider == provider,
            )).scalar_one_or_none() or ""

    def set_session(self, psid: str, provider: str, session_id: str,
                    prompt_digest: str = "") -> None:
        with self.database.transaction() as connection:
            connection.execute(pg_insert(agent_sessions).values(
                psid=psid, provider=provider, session_id=session_id,
                last_seen_at=dt.datetime.now(dt.UTC), prompt_digest=prompt_digest,
            ).on_conflict_do_update(index_elements=[agent_sessions.c.psid], set_={
                "provider": provider, "session_id": session_id,
                "last_seen_at": dt.datetime.now(dt.UTC), "prompt_digest": prompt_digest,
            }))

    def clear_session(self, psid: str, provider: str | None = None) -> None:
        where = [agent_sessions.c.psid == psid]
        if provider:
            where.append(agent_sessions.c.provider == provider)
        with self.database.transaction() as connection:
            connection.execute(agent_sessions.delete().where(*where))

    def handoff(self, psid: str) -> str:
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(handoffs.c.body).where(
                handoffs.c.psid == psid)).scalar_one_or_none() or ""

    def set_handoff(self, psid: str, text: str) -> None:
        with self.database.transaction() as connection:
            previous = connection.execute(sa.select(handoffs.c.body).where(
                handoffs.c.psid == psid).with_for_update()).scalar_one_or_none() or ""
            joined = f"{previous}\n\n{text}".strip() if previous else text.strip()
            connection.execute(pg_insert(handoffs).values(
                psid=psid, body=joined[-20_000:], created_at=dt.datetime.now(dt.UTC),
            ).on_conflict_do_update(index_elements=[handoffs.c.psid], set_={
                "body": joined[-20_000:], "created_at": dt.datetime.now(dt.UTC)}))

    def clear_handoff(self, psid: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(handoffs.delete().where(handoffs.c.psid == psid))

    def mark_inbound(self, psid: str) -> None:
        now = dt.datetime.now(dt.UTC)
        with self.database.transaction() as connection:
            connection.execute(pg_insert(inbound_activity_states).values(
                psid=psid, last_inbound_at=now,
            ).on_conflict_do_update(index_elements=[inbound_activity_states.c.psid],
                                    set_={"last_inbound_at": now}))

    def claim_inbound(self, message_id: str, psid: str, kind: str, body: str, *,
                      reply_to_message_id: str = "") -> bool:
        """Réserve durablement un événement Meta avant de le mettre en file."""
        with self.database.transaction() as connection:
            claimed = connection.execute(pg_insert(inbound_messages).values(
                message_id=message_id, psid=psid, kind=kind, body=body,
                reply_to_message_id=reply_to_message_id or None,
            ).on_conflict_do_nothing().returning(
                inbound_messages.c.message_id)).scalar_one_or_none()
            conversation_body = (safe_conversation_label(body)
                                 if kind == "attachment" else body.strip())
            if claimed is not None and conversation_body:
                conversation_id = connection.execute(pg_insert(conversation_messages).values(
                    id=uuid.uuid4(), psid=psid, role="athlete", body=conversation_body,
                    source_key=f"inbound:{message_id}").on_conflict_do_nothing().returning(
                        conversation_messages.c.id)).scalar_one_or_none()
                if conversation_id is not None:
                    connection.execute(pg_insert(messenger_message_refs).values(
                        message_id=message_id, conversation_message_id=conversation_id,
                        body=conversation_body).on_conflict_do_nothing())
        return claimed is not None

    def record_outbound(self, psid: str, text: str, *, source_key: str,
                        proactive: bool = False,
                        resolve_questions: bool = True,
                        platform_messages: tuple[tuple[str, str], ...] = (),
                        turn_id: str = "",
                        questions: tuple[str, ...] = ()) -> None:
        body = text.strip()
        if not body:
            return
        with self.database.transaction() as connection:
            message_id = connection.execute(pg_insert(conversation_messages).values(
                id=uuid.uuid4(), psid=psid,
                role="proactive" if proactive else "coach", body=body,
                source_key=source_key).on_conflict_do_nothing().returning(
                    conversation_messages.c.id)).scalar_one_or_none()
            created = message_id is not None
            if message_id is None:
                message_id = connection.execute(sa.select(conversation_messages.c.id).where(
                    conversation_messages.c.psid == psid,
                    conversation_messages.c.source_key == source_key,
                )).scalar_one_or_none()
            if message_id is None:
                return
            question_platform_ids: dict[str, str] = {}
            for platform_id, platform_body in platform_messages:
                clean_id, clean_body = platform_id.strip(), platform_body.strip()
                if clean_id and clean_body:
                    connection.execute(pg_insert(messenger_message_refs).values(
                        message_id=clean_id, conversation_message_id=message_id,
                        body=clean_body).on_conflict_do_nothing())
            if not created:
                return
            # Une livraison ne constitue jamais une preuve que l'athlète a répondu. Une
            # question ouverte naît d'une déclaration typée du tour (`fact-write.py
            # ask`) retrouvée dans le texte réellement livré, jamais d'un « ? » de
            # prose : une question rhétorique n'est pas un agenda à suivre. Un rendu
            # fermé n'a pas de tour : le service nomme lui-même l'information qu'il
            # attend (`questions`), soumise au même contrôle de présence dans le texte
            # livré. Sans cela, une relance fixe restait invisible à la mémoire des
            # questions et pouvait se reposer (audit du 2026-09-30, constat 4).
            declared = [{"text": item} for item in questions if item.strip()]
            if turn_id and resolve_questions:
                declared += list(connection.execute(sa.select(turn_commands.c.payload).where(
                    turn_commands.c.turn_id == uuid.UUID(turn_id),
                    turn_commands.c.kind == commands.QUESTION_ASK,
                    turn_commands.c.applied_at.is_not(None),
                )).scalars())
            for item in declared:
                asked_text = item["text"].strip()
                if asked_text not in body:
                    continue
                fingerprint = question_fingerprint(asked_text)
                for platform_id, platform_body in platform_messages:
                    if asked_text in platform_body and platform_id.strip():
                        question_platform_ids[fingerprint] = platform_id.strip()
                for key in item.get("cells", []):
                    connection.execute(pg_insert(proactive_markers).values(
                        marker_key="asked:" + key).on_conflict_do_nothing())
                connection.execute(pg_insert(conversation_questions).values(
                    id=uuid.uuid4(), psid=psid, fingerprint=fingerprint,
                    question=asked_text, source_message_id=message_id,
                    source_platform_message_id=question_platform_ids.get(fingerprint),
                ).on_conflict_do_nothing(
                    index_elements=[conversation_questions.c.psid,
                                    conversation_questions.c.fingerprint],
                    index_where=conversation_questions.c.status.in_(("open", "deferred"))))

    def question_known(self, psid: str, text: str) -> bool:
        """Cette question exacte attend-elle déjà, ou vient-elle d'être soldée ?

        Ouverte ou reportée : la reposer serait une relance, et la reprise d'un report
        appartient au coach, qui a le contexte ; un gabarit n'en a pas. Soldée depuis
        moins de trois jours (répondue, refusée, impossible à préciser) : même fait,
        même réponse. Au-delà, le même texte peut désigner un fait NOUVEAU (« EF 7 km »
        revient chaque semaine) : le texte d'un gabarit n'est pas l'identité d'un fait.
        """
        recent = dt.datetime.now(dt.UTC) - dt.timedelta(days=3)
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(conversation_questions.c.id).where(
                conversation_questions.c.psid == psid,
                conversation_questions.c.fingerprint == question_fingerprint(text),
                sa.or_(conversation_questions.c.status.in_(("open", "deferred")),
                       conversation_questions.c.resolved_at >= recent),
            ).limit(1)).first() is not None

    def expire_stale_questions(self, psid: str, *, older_than_days: int = 14) -> int:
        """Ferme les questions ouvertes restées sans examen : un contexte, pas une dette."""
        limit = dt.datetime.now(dt.UTC) - dt.timedelta(days=older_than_days)
        with self.database.transaction() as connection:
            return connection.execute(conversation_questions.update().where(
                conversation_questions.c.psid == psid,
                conversation_questions.c.status == "open",
                conversation_questions.c.asked_at < limit,
            ).values(status="expired", resolved_at=dt.datetime.now(dt.UTC),
                     review_note=f"Fermée sans examen après {older_than_days} jours."
                     )).rowcount

    def begin_outbound_delivery(self, source_key: str, psid: str,
                                payload_digest: str, payload: dict | None = None) -> str:
        """Réserve la frontière réseau ; `uncertain` interdit un rejeu aveugle.

        Une enveloppe ``failed`` ou ``prepared`` n'a livré aucun fragment : elle peut
        partir, même recalculée. Une enveloppe déjà livrée n'est jamais renvoyée, même
        si le contenu proposé diffère : l'entrant a déjà eu sa réponse.
        """
        now = dt.datetime.now(dt.UTC)
        with self.database.transaction() as connection:
            connection.execute(outbound_deliveries.delete().where(
                outbound_deliveries.c.status != "sending",
                outbound_deliveries.c.updated_at < now - dt.timedelta(days=90)))
            row = connection.execute(sa.select(outbound_deliveries).where(
                outbound_deliveries.c.source_key == source_key
            ).with_for_update()).mappings().one_or_none()
            if row is None:
                connection.execute(outbound_deliveries.insert().values(
                    source_key=source_key, psid=psid, payload_digest=payload_digest,
                    payload=payload, status="sending", attempts=1, updated_at=now))
                return "send"
            if row["psid"] != psid:
                raise ValueError(f"collision d'enveloppe sortante : {source_key}")
            if row["status"] == "delivered":
                return "delivered"
            if row["status"] == "sending":
                return "uncertain"
            attempts = row["attempts"] + (0 if row["status"] == "prepared" else 1)
            connection.execute(outbound_deliveries.update().where(
                outbound_deliveries.c.source_key == source_key).values(
                    payload_digest=payload_digest, payload=payload,
                    status="sending", attempts=attempts,
                    last_error="", updated_at=now))
            return "send"

    def prepare_outbound_delivery(self, source_key: str, psid: str,
                                  payload_digest: str, payload: dict) -> bool:
        """Conserve une sortie calculée mais retenue ; False si elle est déjà partie."""
        now = dt.datetime.now(dt.UTC)
        with self.database.transaction() as connection:
            row = connection.execute(sa.select(outbound_deliveries).where(
                outbound_deliveries.c.source_key == source_key
            ).with_for_update()).mappings().one_or_none()
            if row is None:
                connection.execute(outbound_deliveries.insert().values(
                    source_key=source_key, psid=psid, payload_digest=payload_digest,
                    payload=payload, status="prepared", attempts=1, updated_at=now))
                return True
            if row["psid"] != psid:
                raise ValueError(f"collision d'enveloppe sortante : {source_key}")
            if row["status"] not in {"prepared", "failed"}:
                return False
            connection.execute(outbound_deliveries.update().where(
                outbound_deliveries.c.source_key == source_key).values(
                    payload_digest=payload_digest, payload=payload, status="prepared",
                    updated_at=now))
            return True

    def stored_outbound_delivery(self, source_key: str) -> dict | None:
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(
                outbound_deliveries.c.psid, outbound_deliveries.c.status,
                outbound_deliveries.c.payload,
            ).where(outbound_deliveries.c.source_key == source_key)).mappings().one_or_none()
        return dict(row) if row else None

    def discard_outbound_delivery(self, source_key: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(outbound_deliveries.delete().where(
                outbound_deliveries.c.source_key == source_key,
                outbound_deliveries.c.status.in_(("prepared", "failed"))))

    def finish_outbound_delivery(self, source_key: str, *, delivered: bool,
                                 error: str = "") -> None:
        with self.database.transaction() as connection:
            changed = connection.execute(outbound_deliveries.update().where(
                outbound_deliveries.c.source_key == source_key,
                outbound_deliveries.c.status == "sending",
            ).values(status="delivered" if delivered else "failed",
                     last_error="" if delivered else error[:500],
                     updated_at=dt.datetime.now(dt.UTC))).rowcount
            if changed != 1:
                raise KeyError(f"enveloppe sortante non finalisable : {source_key}")

    def reply_context(self, message_ids: tuple[str, ...]) -> str:
        """Rend la relation de reply Meta, sans confondre la citation et le présent.

        Une référence inconnue reste utile (elle prouve l'intention de répondre), mais
        son contenu n'est jamais reconstruit au jugé.
        """
        if not message_ids:
            return ""
        target = conversation_messages.alias("reply_target")
        with self.database.engine.connect() as connection:
            rows = list(connection.execute(sa.select(
                inbound_messages.c.message_id,
                inbound_messages.c.reply_to_message_id,
                sa.case(
                    (target.c.id.is_not(None), messenger_message_refs.c.body),
                    else_=None,
                ).label("reply_body"),
                target.c.role.label("reply_role"),
            ).select_from(inbound_messages.outerjoin(
                messenger_message_refs,
                messenger_message_refs.c.message_id == inbound_messages.c.reply_to_message_id,
            ).outerjoin(
                target,
                sa.and_(target.c.id == messenger_message_refs.c.conversation_message_id,
                        target.c.psid == inbound_messages.c.psid),
            )).where(
                inbound_messages.c.message_id.in_(message_ids),
                inbound_messages.c.reply_to_message_id.is_not(None),
            ).order_by(inbound_messages.c.received_at)).mappings())
        if not rows:
            return ""
        references = []
        for row in rows:
            if row["reply_body"]:
                owner = ("du coach" if row["reply_role"] in {"coach", "proactive"}
                         else "de ⟨athlète⟩")
                references.append(
                    f"Le message courant répond directement à ce message antérieur {owner} :\n"
                    f"« {row['reply_body']} »")
            else:
                references.append(
                    "Le message courant est une réponse directe à un ancien message "
                    "Messenger, mais son contenu n'est plus disponible localement. "
                    "N'invente pas la citation.")
        return ("[RÉPONSE MESSENGER CIBLÉE — invisible]\n"
                + "\n\n".join(references)
                + "\nInterprète ce lien comme une réponse ciblée, pas comme un nouveau sujet. "
                  "Ne récite pas cette note.")

    def issue_strength_link(self, psid: str, session: dict) -> str:
        """Persiste la prescription immuable et rend sa capacité brute éphémère."""
        now = dt.datetime.now(dt.UTC)
        snapshot = strength.expiring_snapshot(session, now=int(now.timestamp()))
        expires_at = dt.datetime.fromtimestamp(snapshot["expires_at"], tz=dt.UTC)
        for _ in range(3):
            token = strength.new_token()
            with self.database.transaction() as connection:
                inserted = connection.execute(pg_insert(strength_session_links).values(
                    token_hash=strength.token_digest(token), psid=psid,
                    session_key=snapshot["sid"], snapshot=snapshot,
                    expires_at=expires_at, created_at=now,
                ).on_conflict_do_nothing().returning(
                    strength_session_links.c.token_hash)).scalar_one_or_none()
            if inserted is not None:
                return token
        raise RuntimeError("collision répétée de jeton de séance")

    def resolve_strength_link(self, psid: str, token: str) -> dict:
        digest = strength.token_digest(token)
        if not digest:
            return {"status": "missing"}
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(strength_session_links).where(
                strength_session_links.c.token_hash == digest,
                strength_session_links.c.psid == psid,
            )).mappings().one_or_none()
        if row is None:
            return {"status": "missing"}
        if row["revoked_at"] is not None:
            return {"status": "revoked"}
        if row["expires_at"] <= dt.datetime.now(dt.UTC):
            return {"status": "expired"}
        return {"status": "active", "session": dict(row["snapshot"])}

    def revoke_strength_link(self, psid: str, token: str) -> bool:
        digest = strength.token_digest(token)
        if not digest:
            return False
        with self.database.transaction() as connection:
            changed = connection.execute(strength_session_links.update().where(
                strength_session_links.c.token_hash == digest,
                strength_session_links.c.psid == psid,
                strength_session_links.c.revoked_at.is_(None),
            ).values(revoked_at=dt.datetime.now(dt.UTC))).rowcount
        return bool(changed)

    def relationship_context(self, psid: str, *, threads: bool = True) -> str:
        """`threads=False` quand le paquet métier du tour porte déjà les fils ouverts."""
        with self.database.engine.connect() as connection:
            # Une phrase d'attente ou de limite est un message livré, pas un tour de
            # fond : comptée parmi les huit, elle en chassait le sujet réel.
            rows = list(connection.execute(sa.select(conversation_messages).where(
                conversation_messages.c.psid == psid,
                sa.not_(sa.or_(*(
                    conversation_messages.c.source_key.like(prefix + ":%")
                    for prefix in SERVICE_LINE_PREFIXES)))).order_by(
                    conversation_messages.c.created_at.desc()).limit(8)).mappings())
            rows.reverse()
            open_rows = list(connection.execute(sa.select(conversation_questions).where(
                conversation_questions.c.psid == psid,
                conversation_questions.c.status.in_(("open", "deferred"))).order_by(
                conversation_questions.c.asked_at)).mappings())
            resolved_rows = list(connection.execute(sa.select(conversation_questions).where(
                conversation_questions.c.psid == psid,
                conversation_questions.c.status.in_(("answered", "expired"))).order_by(
                conversation_questions.c.resolved_at.desc()).limit(12)).mappings())
            thread_rows = list(connection.execute(sa.select(conversation_threads).where(
                conversation_threads.c.athlete_id == ATHLETE,
                conversation_threads.c.status == "ouvert").order_by(
                    conversation_threads.c.opened_on.desc())).mappings()) if threads else []
        return render_memory([dict(row) for row in rows], [dict(row) for row in open_rows],
                             [dict(row) for row in thread_rows],
                             [dict(row) for row in resolved_rows])

    def correction_context(self, text: str) -> str:
        tokens = set(re.findall(r"[a-zà-ÿ0-9]{4,}", text.lower())) - _RELATION_STOPWORDS
        if not tokens:
            return ""
        with self.database.engine.connect() as connection:
            rows = list(connection.execute(sa.select(correction_deliveries).where(
                correction_deliveries.c.athlete_id == ATHLETE).order_by(
                    correction_deliveries.c.registered_at.desc()).limit(50)).mappings())
        scored = []
        for row in rows:
            words = (set(re.findall(r"[a-zà-ÿ0-9]{4,}", row["summary"].lower()))
                     - _RELATION_STOPWORDS)
            score = len(tokens & words)
            if score:
                scored.append((score, row["summary"]))
        if not scored:
            return ""
        selected = [summary for _, summary in sorted(scored, reverse=True)[:3]]
        return ("[CORRECTIONS PERTINENTES — invisible, à ne pas réciter]\n- "
                + "\n- ".join(selected))

    def pending_inbound(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(
                inbound_messages.c.message_id, inbound_messages.c.psid,
                inbound_messages.c.kind, inbound_messages.c.body,
            ).where(inbound_messages.c.status == "pending").order_by(
                inbound_messages.c.received_at)).mappings()
            return [dict(row) for row in rows]

    def inbound_received_at(self, message_ids: tuple[str, ...]) -> float | None:
        """Heure de réception du plus ancien message d'un lot, None s'il est inconnu."""
        if not message_ids:
            return None
        with self.database.engine.connect() as connection:
            value = connection.execute(sa.select(
                sa.func.min(inbound_messages.c.received_at)).where(
                inbound_messages.c.message_id.in_(message_ids))).scalar_one_or_none()
        return _epoch(value) or None

    def complete_inbound(self, message_ids: tuple[str, ...]) -> None:
        if not message_ids:
            return
        with self.database.transaction() as connection:
            connection.execute(inbound_messages.update().where(
                inbound_messages.c.message_id.in_(message_ids)
            ).values(
                status="done", completed_at=dt.datetime.now(dt.UTC),
                body=sa.case((inbound_messages.c.kind == "attachment", ""),
                             else_=inbound_messages.c.body)))

    def last_inbound(self, psid: str) -> float:
        with self.database.engine.connect() as connection:
            value = connection.execute(sa.select(
                inbound_activity_states.c.last_inbound_at).where(
                inbound_activity_states.c.psid == psid)).scalar_one_or_none()
        return _epoch(value)

    def within_24h(self, psid: str) -> bool:
        return time.time() - self.last_inbound(psid) < 24 * 3600

    def claim_patience(self, min_gap_seconds: float) -> bool:
        now = dt.datetime.now(dt.UTC)
        with self.database.transaction() as connection:
            connection.execute(sa.text(
                "SELECT pg_advisory_xact_lock(hashtext('redline:patience'))"))
            last_sent = connection.execute(sa.select(
                patience_notifications.c.last_sent_at).where(
                    patience_notifications.c.singleton.is_(True)
                ).with_for_update()).scalar_one_or_none()
            if last_sent and now.timestamp() - _epoch(last_sent) < min_gap_seconds:
                return False
            connection.execute(pg_insert(patience_notifications).values(
                singleton=True, last_sent_at=now,
            ).on_conflict_do_update(index_elements=[patience_notifications.c.singleton],
                                    set_={"last_sent_at": now}))
            return True

    def owe_plan(self, psid: str, week: str, revision: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(pg_insert(delivery_debts).values(
                psid=psid, iso_week=week, revision=revision,
            ).on_conflict_do_update(index_elements=[delivery_debts.c.psid], set_={
                "iso_week": week, "revision": revision, "created_at": sa.func.now()}))

    def plan_owed(self, psid: str) -> tuple[str, int]:
        with self.database.engine.connect() as connection:
            row = connection.execute(sa.select(delivery_debts).where(
                delivery_debts.c.psid == psid)).mappings().one_or_none()
        return (row["iso_week"], row["revision"]) if row else ("", 0)

    def settle_plan(self, psid: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(delivery_debts.delete().where(delivery_debts.c.psid == psid))

    def missed_session_is_current(self, job_id: str) -> bool:
        """Une relance attendue dans la file peut avoir perdu sa cause depuis."""
        if not job_id.startswith("missed-session:"):
            return False
        try:
            cycle_id = uuid.UUID(job_id.removeprefix("missed-session:"))
        except ValueError:
            return False
        store = OperationalStore(self.database)
        with self.database.transaction() as connection:
            store.reconcile_strength_cycles_in(connection)
            row = connection.execute(sa.select(
                session_cycles.c.state, planned_sessions.c.scheduled_on,
                planned_sessions.c.workout,
            ).join(planned_sessions,
                   planned_sessions.c.id == session_cycles.c.source_session_id).where(
                session_cycles.c.id == cycle_id, session_cycles.c.athlete_id == ATHLETE,
            )).mappings().one_or_none()
            return bool(row and row["state"] == "missed" and not store.strength_evidence_in(
                connection, row["scheduled_on"], row["workout"]))

    def materialize_intelligence(self, now: dt.datetime) -> int:
        """Transforme les faits typés en travaux durables, une seule fois par cause.

        Une transaction, une méthode par cause : chacune rend le nombre de travaux créés.
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=session_cycle.LOCAL_TIMEZONE)
        local = now.astimezone(session_cycle.LOCAL_TIMEZONE)
        utc = now.astimezone(dt.UTC)
        with self.database.transaction() as connection:
            store = OperationalStore(self.database)
            store.reconcile_strength_cycles_in(connection)
            # Une prédiction rattachée dont la date est passée sans observation n'a pas
            # été testée : elle sort de la dette en `void`, jamais en faux.
            store.settle_unobserved_predictions_in(connection, local.date(),
                                                   actor="intelligence")
            predictions = connection.execute(sa.select(coach_predictions).where(
                coach_predictions.c.athlete_id == ATHLETE,
                coach_predictions.c.status == "open")).mappings().all()
            return (self._materialize_cycles(connection, store, local, utc)
                    + self._materialize_races(connection, utc)
                    + self._materialize_corrections(connection, utc)
                    + self._materialize_challenges(connection, predictions, utc)
                    + self._materialize_daily_signal(connection, store, predictions,
                                                     local, utc)
                    + self._materialize_gear(connection, utc))

    @staticmethod
    def _materialize_cycles(connection, store: OperationalStore, local: dt.datetime,
                            utc: dt.datetime) -> int:
        """Séance manquée, retour digestif."""
        created = 0
        cycles = connection.execute(sa.select(
            session_cycles, weekly_plans.c.iso_week, planned_sessions.c.title,
            planned_sessions.c.scheduled_on, planned_sessions.c.kind,
            planned_sessions.c.workout, planned_sessions.c.carbs_target_g_h,
        ).join(weekly_plans, weekly_plans.c.id == session_cycles.c.plan_id).join(
            planned_sessions,
            planned_sessions.c.id == session_cycles.c.source_session_id).where(
                session_cycles.c.athlete_id == ATHLETE,
                session_cycles.c.state.in_(("planned", "started", "completed", "closed")),
        ).with_for_update(of=session_cycles)).mappings().all()
        for row in cycles:
            detail = {"session_key": f"{row['iso_week']}:{row['logical_key']}",
                      "title": row["title"], "workout": row["workout"]}
            if row["state"] == "planned" and row["kind"] != "rest":
                if store.strength_evidence_in(
                        connection, row["scheduled_on"], row["workout"]):
                    continue
                missed_at = ((row["planned_for"] + dt.timedelta(hours=4))
                             if row["planned_for"] else dt.datetime.combine(
                                 row["scheduled_on"], dt.time(22, 0),
                                 tzinfo=session_cycle.LOCAL_TIMEZONE))
                if local >= missed_at:
                    connection.execute(session_cycles.update().where(
                        session_cycles.c.id == row["id"]).values(
                            state="missed", updated_at=utc))
                    if PostgresProactiveState._enqueue(
                            connection, f"missed-session:{row['id']}", "missed_session",
                            json.dumps(detail, ensure_ascii=False), utc.timestamp()):
                        created += 1
                    continue
            if row["carbs_target_g_h"] is not None and row["observation_id"]:
                pending = connection.execute(sa.select(
                    observation_metrics.c.gut_symptoms_state).where(
                        observation_metrics.c.observation_id == row["observation_id"]
                    )).scalar_one()
                if pending == "pending" and PostgresProactiveState._enqueue(
                        connection, f"gut-review:{row['id']}", "gut_review",
                        json.dumps({**detail, "target_g_h": row["carbs_target_g_h"]},
                                   ensure_ascii=False), utc.timestamp()):
                    created += 1
        return created

    @staticmethod
    def _materialize_races(connection, utc: dt.datetime) -> int:
        """La feuille de route de chaque course, recalée quand sa date bouge."""
        created = 0
        races = connection.execute(sa.select(deadlines).where(
            deadlines.c.athlete_id == ATHLETE, deadlines.c.kind == "race")).mappings()
        for race in races:
            prefix = f"race:{race['id']}:"
            # Une date déplacée ou une course clôturée annule le vieux réveil :
            # l'identité de la course reste, celle du calendrier change.
            obsolete = proactive_jobs.c.job_key.startswith(prefix, autoescape=True)
            if race["status"] != "done":
                obsolete = sa.and_(obsolete, ~proactive_jobs.c.job_key.startswith(
                    f"{prefix}{race['due_on'].isoformat()}:", autoescape=True))
            connection.execute(proactive_jobs.update().where(
                obsolete, proactive_jobs.c.status.in_(("pending", "running"))
            ).values(status="done", completed_at=utc))
            if race["status"] == "done":
                continue
            for checkpoint in compile_deadline(dict(race)):
                if checkpoint.expires_at >= utc:
                    created += int(PostgresProactiveState._enqueue(
                        connection, checkpoint.key, checkpoint.kind,
                        json.dumps(checkpoint.detail, ensure_ascii=False,
                                   sort_keys=True, separators=(",", ":")),
                        checkpoint.due_at.timestamp(),
                        expire_at=checkpoint.expires_at.timestamp()))
        return created

    @staticmethod
    def _materialize_corrections(connection, utc: dt.datetime) -> int:
        """Une erreur du coach corrigée et pas encore annoncée, dans sa semaine."""
        created = 0
        corrections = connection.execute(sa.select(correction_deliveries).where(
            correction_deliveries.c.athlete_id == ATHLETE,
            correction_deliveries.c.delivered_at.is_(None))).mappings().all()
        for correction in corrections:
            expires = correction["registered_at"] + CORRECTION_ANNOUNCE_WINDOW
            if expires <= utc:
                # Une annonce déjà en file depuis avant cette règle expire aussi, comme
                # le fait `_expire_in` pour toute échéance dépassée.
                connection.execute(proactive_jobs.update().where(
                    sa.or_(proactive_jobs.c.job_key == f"correction:{correction['correction_key']}",
                           proactive_jobs.c.job_key.like(f"correction-id:{correction['id']}:%")),
                    proactive_jobs.c.status == "pending").values(
                        status="failed", completed_at=utc, last_error="expiré avant livraison"))
                continue
            created += int(PostgresProactiveState._enqueue(
                connection, f"correction:{correction['correction_key']}", "correction",
                correction["summary"], utc.timestamp(), expire_at=expires.timestamp(),
                priority=True))
        return created

    @staticmethod
    def _materialize_challenges(connection, predictions: Sequence[RowMapping],
                                utc: dt.datetime) -> int:
        """Une hausse de charge ouverte attend sa contradiction."""
        created = 0
        for prediction in predictions:
            if prediction["direction"] == "increase" and not prediction["challenge"]:
                created += int(PostgresProactiveState._enqueue(
                    connection, f"challenge:{prediction['prediction_key']}",
                    "decision_challenge", json.dumps({
                        "key": prediction["prediction_key"],
                        "statement": prediction["statement"],
                        "probability": float(prediction["probability"]),
                        "due_on": prediction["due_on"].isoformat()}, ensure_ascii=False),
                    utc.timestamp(), priority=True))
        return created

    @staticmethod
    def _materialize_daily_signal(connection, store: OperationalStore,
                                  predictions: Sequence[RowMapping],
                                  local: dt.datetime, utc: dt.datetime) -> int:
        """La sentinelle : signaux faibles, envie, prédictions échues, séances manquées."""
        created = 0
        # Même lecture que la CLI et le dossier du bilan : registre typé et
        # cadence travaillée exprès, sur une seule définition (`signals.current`).
        signals = [str(signal) for signal in signal_model.current(store)]
        recent_daily = list(connection.execute(sa.select(daily_checkins).where(
            daily_checkins.c.athlete_id == ATHLETE,
            daily_checkins.c.checked_on >= local.date() - dt.timedelta(days=21),
        ).order_by(daily_checkins.c.checked_on.desc()).limit(3)).mappings())
        if len(recent_daily) == 3 and all(row["desire"] == "basse" for row in recent_daily):
            signals.append("envie basse sur les trois dernières lectures")
        due_predictions = [row for row in predictions if row["due_on"] <= local.date()]
        if due_predictions:
            signals.append(f"{len(due_predictions)} prédiction(s) échue(s) à résoudre")
        missed_count = connection.execute(sa.select(sa.func.count()).select_from(
            session_cycles).join(planned_sessions,
                planned_sessions.c.id == session_cycles.c.source_session_id).where(
                session_cycles.c.athlete_id == ATHLETE,
                session_cycles.c.state == "missed",
                session_cycles.c.updated_at >= utc - dt.timedelta(days=21),
                ~sa.exists().where(
                    strength_sessions.c.athlete_id == ATHLETE,
                    strength_sessions.c.performed_on == planned_sessions.c.scheduled_on,
                    planned_sessions.c.workout == sa.literal("renfo-") +
                        strength_sessions.c.workout,
                ))).scalar_one()
        if missed_count >= 2:
            signals.append(f"{missed_count} séances manquées sur 21 jours")
        if signals:
            created += int(PostgresProactiveState._enqueue(
                connection, f"daily-signal:{local.date().isoformat()}", "daily_signal",
                json.dumps(signals, ensure_ascii=False), utc.timestamp(), priority=True))
        return created

    @staticmethod
    def _materialize_gear(connection, utc: dt.datetime) -> int:
        """Une paire active à 90 % de son seuil d’inspection."""
        created = 0
        distance = sa.case((observation_metrics.c.distance_km_state == "known",
                            observation_metrics.c.distance_km), else_=0)
        local_gear_distance = sa.func.coalesce(sa.func.sum(distance), 0)
        gear_rows = list(connection.execute(sa.select(
            gear_items.c.gear_id, gear_items.c.name, gear_items.c.role,
            gear_items.c.retire_at_km,
            sa.func.greatest(
                local_gear_distance,
                sa.func.coalesce(gear_items.c.strava_distance_km, 0)).label("km"),
            gear_items.c.distance_synced_at,
        ).outerjoin(observations, sa.and_(
            observations.c.gear_id == gear_items.c.gear_id,
            observations.c.athlete_id == gear_items.c.athlete_id,
        )).outerjoin(observation_metrics,
                    observation_metrics.c.observation_id == observations.c.id).where(
            gear_items.c.athlete_id == ATHLETE, gear_items.c.status == "active"
        ).group_by(gear_items.c.gear_id)).mappings())
        for gear in gear_rows:
            km, limit = float(gear["km"]), float(gear["retire_at_km"])
            if km >= limit * .9:
                created += int(PostgresProactiveState._enqueue(
                    connection, f"gear-alert:{gear['gear_id']}:{int(limit)}", "gear_alert",
                    json.dumps({"name": gear["name"], "km": round(km, 1),
                                "limit": limit,
                                "synced_at": (gear["distance_synced_at"].isoformat()
                                              if gear["distance_synced_at"] else None)},
                               ensure_ascii=False), utc.timestamp()))
        return created

    def ingest_strava_activity(self, activity_id: int, *, fresh_words: bool = False) -> dict:
        """Capture et enrichit les faits objectifs avant de réveiller l'agent."""
        from scripts.storage.repository import OperationalStore

        from .activity_pipeline import ActivityPipeline

        store = OperationalStore(self.database)
        try:
            return ActivityPipeline(store).run(activity_id, fresh_words=fresh_words)
        except Exception as exc:
            store.record_strava_failure(activity_id, str(exc))
            raise

class PostgresProactiveState(ProactiveState):
    def __init__(self, store: PostgresStateStore) -> None:
        self.store = store

    @property
    def database(self) -> Database:
        return self.store.database

    def record_activity(self, activity_id: str, due: float) -> None:
        with self.database.transaction() as connection:
            connection.execute(pg_insert(athlete_activity_states).values(
                athlete_id=ATHLETE, last_activity_at=dt.datetime.now(dt.UTC),
            ).on_conflict_do_update(index_elements=[athlete_activity_states.c.athlete_id],
                                    set_={"last_activity_at": dt.datetime.now(dt.UTC)}))
            self._enqueue(connection, f"strava:{activity_id}", "strava_debrief",
                          str(activity_id), due, priority=True)

    def last_activity(self) -> float:
        with self.database.engine.connect() as connection:
            value = connection.execute(sa.select(
                athlete_activity_states.c.last_activity_at).where(
                athlete_activity_states.c.athlete_id == ATHLETE)).scalar_one_or_none()
        return _epoch(value)

    @staticmethod
    def _enqueue(connection, job_id: str, kind: str, detail: str, due: float,
                 *, expire_at: float = 0.0, priority: bool = False) -> bool:
        return connection.execute(pg_insert(proactive_jobs).values(
            job_key=job_id, kind=kind, detail=detail, due_at=_at(due),
            expires_at=_at(expire_at) if expire_at else None, priority=priority,
        ).on_conflict_do_nothing().returning(proactive_jobs.c.job_key)).scalar_one_or_none() is not None

    def enqueue_job(self, job_id: str, kind: str, detail: str, due: float,
                    *, expire_at: float = 0.0, priority: bool = False) -> bool:
        with self.database.transaction() as connection:
            return self._enqueue(connection, job_id, kind, detail, due,
                                 expire_at=expire_at, priority=priority)

    def claim_due_job(self, now: float, *, allow_ordinary: bool = True) -> dict | None:
        current = _at(now)
        with self.database.transaction() as connection:
            self._expire_in(connection, current)
            query = sa.select(proactive_jobs).where(
                proactive_jobs.c.status == "pending", proactive_jobs.c.due_at <= current)
            if not allow_ordinary:
                query = query.where(proactive_jobs.c.priority.is_(True))
            rows = connection.execute(query.order_by(
                proactive_jobs.c.priority.desc(), proactive_jobs.c.due_at
            ).with_for_update(skip_locked=True)).mappings()
            for row in rows:
                attempts = row["attempts"] + 1
                connection.execute(proactive_jobs.update().where(
                    proactive_jobs.c.job_key == row["job_key"]
                ).values(status="running", attempts=attempts))
                return self._job(dict(row), attempts=attempts, status="running")
            return None

    def claim_job(self, job_id: str) -> dict | None:
        """Réclame UN job en attente, dû ou non : le tour qui l'absorbe le porte."""
        with self.database.transaction() as connection:
            row = connection.execute(sa.select(proactive_jobs).where(
                proactive_jobs.c.job_key == job_id, proactive_jobs.c.status == "pending",
            ).with_for_update(skip_locked=True)).mappings().one_or_none()
            if row is None:
                return None
            attempts = row["attempts"] + 1
            connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == job_id
            ).values(status="running", attempts=attempts))
            return self._job(dict(row), attempts=attempts, status="running")

    def hasten_job(self, job_id: str, due: float) -> bool:
        """Avance l'échéance d'un job encore en attente ; ne la recule jamais."""
        with self.database.transaction() as connection:
            return connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == job_id, proactive_jobs.c.status == "pending",
                proactive_jobs.c.due_at > _at(due),
            ).values(due_at=_at(due))).rowcount == 1

    @staticmethod
    def _expire_in(connection, current: dt.datetime) -> int:
        rows = connection.execute(sa.select(proactive_jobs).where(
            proactive_jobs.c.status == "pending",
            proactive_jobs.c.expires_at.is_not(None),
            proactive_jobs.c.expires_at < current,
        ).with_for_update(skip_locked=True)).mappings().all()
        for row in rows:
            connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == row["job_key"]
            ).values(status="failed", completed_at=current,
                     last_error="expiré avant livraison"))
        return len(rows)

    def expire_jobs(self, now: float) -> int:
        with self.database.transaction() as connection:
            return self._expire_in(connection, _at(now))

    @staticmethod
    def _job(row: dict, **updates) -> dict:
        data = {"id": row["job_key"], "kind": row["kind"], "detail": row["detail"],
                "due": _epoch(row["due_at"]), "expire_at": _epoch(row["expires_at"]),
                "priority": row["priority"], "status": row["status"],
                "attempts": row["attempts"], "last_error": row.get("last_error", "")}
        data.update(updates)
        return data

    def complete_job(self, job_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == job_id
            ).values(status="done", completed_at=dt.datetime.now(dt.UTC)))
            self._purge_old_jobs(connection)

    @staticmethod
    def _purge_old_jobs(connection) -> None:
        done = connection.execute(sa.select(proactive_jobs.c.job_key).where(
            proactive_jobs.c.status.in_(("done", "failed"))).order_by(
                proactive_jobs.c.completed_at.desc()).offset(JOB_KEEP)).scalars().all()
        if done:
            connection.execute(proactive_jobs.delete().where(
                proactive_jobs.c.job_key.in_(done)))

    def retry_job(self, job_id: str, due: float, expire_at: float) -> None:
        with self.database.transaction() as connection:
            result = connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == job_id,
                proactive_jobs.c.status.in_(("pending", "running")),
            ).values(status="pending", due_at=_at(due), expires_at=_at(expire_at)))
            if result.rowcount != 1:
                raise KeyError(f"job proactif inconnu ou déjà soldé : {job_id}")

    def reopen_job(self, job_id: str, due: float, expire_at: float) -> None:
        """Rouvre explicitement un job terminé après une réparation opérateur.

        Ce n'est pas `retry_job` : une reprise manuelle efface l'acquittement ancien,
        sinon une ligne `pending` conserverait paradoxalement son `completed_at`.
        """
        with self.database.transaction() as connection:
            result = connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == job_id,
                proactive_jobs.c.status.in_(("done", "failed")),
            ).values(status="pending", due_at=_at(due), expires_at=_at(expire_at),
                     completed_at=None, attempts=0, last_error=""))
            if result.rowcount != 1:
                raise KeyError(f"job proactif terminé inconnu : {job_id}")

    def release_job(self, job_id: str, delay: float = 60.0, *,
                    expire_at: float = 0.0) -> bool:
        """Remet un job réclamé en file, toujours avec une échéance.

        Rend False quand la nouvelle échéance dépasse déjà l'expiration : le job est
        alors soldé en `failed`, jamais relâché sans fin.
        """
        due = _at(time.time() + delay)
        fallback = _at(expire_at) if expire_at else None
        with self.database.transaction() as connection:
            row = connection.execute(sa.select(proactive_jobs.c.expires_at).where(
                proactive_jobs.c.job_key == job_id, proactive_jobs.c.status == "running",
            ).with_for_update()).one_or_none()
            if row is None:
                return False
            expires = row.expires_at or fallback
            if expires is not None and due > expires:
                connection.execute(proactive_jobs.update().where(
                    proactive_jobs.c.job_key == job_id).values(
                        status="failed", completed_at=dt.datetime.now(dt.UTC),
                        last_error="expiré avant livraison"))
                return False
            # Un report n'est pas une tentative : seul un échec consomme le plafond.
            connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == job_id).values(
                    status="pending", due_at=due, expires_at=expires,
                    attempts=sa.func.greatest(proactive_jobs.c.attempts - 1, 0)))
            return True

    def fail_job(self, job_id: str, cause: str) -> None:
        """Solde un job non livré en gardant sa cause, distincte d'un succès."""
        with self.database.transaction() as connection:
            connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.job_key == job_id,
                proactive_jobs.c.status.in_(("pending", "running")),
            ).values(status="failed", completed_at=dt.datetime.now(dt.UTC),
                     last_error=cause[:500]))
            self._purge_old_jobs(connection)

    def recover_running_jobs(self) -> int:
        """Reprend les claims de l'ancien processus au démarrage du service."""
        with self.database.transaction() as connection:
            return connection.execute(proactive_jobs.update().where(
                proactive_jobs.c.status == "running"
            ).values(status="pending", due_at=sa.func.least(
                proactive_jobs.c.due_at, dt.datetime.now(dt.UTC)))).rowcount

    def jobs(self, *, active_only: bool = False) -> list[dict]:
        query = sa.select(proactive_jobs).order_by(proactive_jobs.c.due_at)
        if active_only:
            query = query.where(proactive_jobs.c.status.in_(("pending", "running")))
        with self.database.engine.connect() as connection:
            return [self._job(dict(row)) for row in connection.execute(query).mappings()]

    def marked(self, name: str) -> bool:
        """Ce marqueur a-t-il déjà été posé ? Un marqueur est une PRÉSENCE de ligne.

        Il portait une `value` dont `"1"` était la seule valeur légale, refusée par un
        `raise` -- le vestige d'un store JSON clé/valeur. Un paramètre à une seule
        valeur acceptable ne documente rien, il invite à en essayer une autre : le
        double de test, lui, acceptait tout ce qu'on lui donnait.
        """
        with self.database.engine.connect() as connection:
            return bool(connection.execute(sa.select(sa.exists().where(
                proactive_markers.c.marker_key == name))).scalar())

    def mark(self, name: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(pg_insert(proactive_markers).values(
                marker_key=name).on_conflict_do_nothing())

    def _budget(self, connection, now: dt.datetime, *, mutate: bool) -> dict:
        query = sa.select(proactive_delivery_budgets).where(
            proactive_delivery_budgets.c.budget_on == now.date())
        if mutate:
            query = query.with_for_update()
        row = connection.execute(query).mappings().one_or_none()
        if not row:
            return {"budget_on": now.date(), "sent_count": 0, "last_sent_at": None}
        return dict(row)

    def would_pass(self, now: dt.datetime, config: Config, ignore_gap: bool = False) -> bool:
        with self.database.engine.connect() as connection:
            budget = self._budget(connection, now, mutate=False)
        if not config.in_active_window(now.time()):
            return False
        return ignore_gap or now.timestamp() - _epoch(budget["last_sent_at"]) >= \
            config.proactive_min_gap_hours * 3600

    def finish_delivery(self, job_id: str, now: dt.datetime) -> None:
        """Acquitte atomiquement le budget et le job dont le message est parti."""
        with self.database.transaction() as connection:
            connection.execute(sa.text(
                "SELECT pg_advisory_xact_lock(hashtext('redline:proactive-budget'))"))
            budget = self._budget(connection, now, mutate=True)
            connection.execute(pg_insert(proactive_delivery_budgets).values(
                budget_on=now.date(), sent_count=budget["sent_count"] + 1,
                last_sent_at=now,
            ).on_conflict_do_update(
                index_elements=[proactive_delivery_budgets.c.budget_on], set_={
                    "sent_count": budget["sent_count"] + 1, "last_sent_at": now}))
            if job_id:
                result = connection.execute(proactive_jobs.update().where(
                    proactive_jobs.c.job_key == job_id,
                    proactive_jobs.c.status == "running",
                ).values(status="done", completed_at=now))
                if result.rowcount != 1:
                    raise KeyError(f"job proactif non acquittable : {job_id}")
                if job_id.startswith("correction-id:"):
                    correction_id = uuid.UUID(job_id.split(":", 2)[1])
                    connection.execute(correction_deliveries.update().where(
                        correction_deliveries.c.athlete_id == ATHLETE,
                        correction_deliveries.c.id == correction_id).values(
                            delivered_at=now))
                elif job_id.startswith("correction:"):
                    connection.execute(correction_deliveries.update().where(
                        correction_deliveries.c.athlete_id == ATHLETE,
                        correction_deliveries.c.correction_key == job_id.removeprefix(
                            "correction:")).values(delivered_at=now))
                self._purge_old_jobs(connection)
