"""Comptes, purge et jeton de projection."""

from __future__ import annotations

import datetime as dt
from typing import ClassVar

import sqlalchemy as sa

from ..schema import (
    agent_sessions,
    athlete_activity_states,
    athletes,
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
    patience_notifications,
    plan_gates,
    plan_revisions,
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
    strength_report_claims,
    strength_report_daily_quotas,
    strength_results,
    strength_session_links,
    strength_sessions,
    strength_sets,
    turn_commands,
    weekly_instruments,
    weekly_plans,
)
from ._common import StoreBase


class Maintenance(StoreBase):
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
