"""Transactional policies specific to weekly program revisions."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from .database import StorageError
from .schema import observations, plan_gates

GATE_EVIDENCE = re.compile(
    r"^gate:(?P<gate>[a-z0-9][a-z0-9-]+)="
    r"(?P<resolution>observation|fallback|void):(?P<detail>.+)$")


def enforce_gate_resolution(connection: Connection, athlete_id: str,
                            previous_revision: Any, revision: dict) -> None:
    """A gate that disappears must name the canonical fact or fallback that closed it."""
    previous_gates = list(connection.execute(sa.select(plan_gates).where(
        plan_gates.c.revision_id == previous_revision["id"])).mappings())
    if not previous_gates:
        return
    current_ids = {gate["id"] for gate in revision.get("gates", [])}
    removed = {gate["logical_key"]: gate for gate in previous_gates
               if gate["logical_key"] not in current_ids}
    if not removed:
        return

    resolutions: dict[str, tuple[str, str]] = {}
    for item in revision.get("evidence", []):
        match = GATE_EVIDENCE.fullmatch(item.strip())
        if match:
            resolutions[match.group("gate")] = (
                match.group("resolution"), match.group("detail").strip())

    computed_at = revision["computed_at"]
    if isinstance(computed_at, str):
        computed_at = dt.datetime.fromisoformat(computed_at)
    computed_on = computed_at.date()
    for gate_id, gate in removed.items():
        resolution = resolutions.get(gate_id)
        if resolution is None:
            raise StorageError(
                f"gate {gate_id!r} removed without a typed resolution: add "
                f"gate:{gate_id}=observation:<key>, fallback:<reason> or void:<reason> to "
                "evidence")
        kind, detail = resolution
        if kind == "observation":
            observation = connection.execute(sa.select(
                observations.c.occurred_on).where(
                    observations.c.athlete_id == athlete_id,
                    observations.c.idempotency_key == detail,
                )).mappings().one_or_none()
            if observation is None:
                raise StorageError(
                    f"gate {gate_id!r}: unknown canonical observation {detail!r}")
            if not gate["due_on"] <= observation["occurred_on"] <= computed_on:
                raise StorageError(
                    f"gate {gate_id!r}: observation of {observation["occurred_on"]} outside the window "
                    f"{gate["due_on"]}..{computed_on}")
        elif kind == "fallback":
            if computed_on < gate["due_on"]:
                raise StorageError(
                    f"gate {gate_id!r}: fallback before its due date {gate["due_on"]}")
        elif computed_on >= gate["due_on"]:
            raise StorageError(
                f"gate {gate_id!r}: void is forbidden from its due date on; use an observation "
                "or the pre-committed fallback")
