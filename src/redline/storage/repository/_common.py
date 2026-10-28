"""What every aggregate of the store shares: helpers and the base class."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from redline.model import session_kinds

from ..database import Database, StorageError
from ..schema import (
    audit_events,
    projection_states,
    strava_activity_intakes,
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


class StoreBase:
    """The state every aggregate works on. An aggregate may call another one's
    methods: they all end up on `OperationalStore`."""

    database: Database
    athlete_id: str

    if TYPE_CHECKING:
        def __getattr__(self, name: str) -> Any: ...
