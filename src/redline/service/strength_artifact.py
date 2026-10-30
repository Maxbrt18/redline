"""Produces the strength card from the plan's typed prescription."""

from __future__ import annotations

from pathlib import Path

from redline.cards import strength_card
from redline.model import session_kinds

from . import strength
from .outbox import Outbox

STRENGTH_CARD_PREFIX = "strength-"


def render_strength_artifact(session: dict, outbox: Outbox, today: str, *,
                             root: Path | None = None) -> Path:
    target = outbox.target(f"{STRENGTH_CARD_PREFIX}{today}.png")
    selection = selection_for(session, today)
    _, built = strength_card.render_card(selection, target, root=root)
    strength.write_sidecar(
        target, strength_card.subtitle(built), strength_card.snapshot(built, root=root))
    return target


def selection_for(session: dict, today: str) -> dict:
    spec = session.get("strength")
    if not isinstance(spec, dict):
        raise ValueError("no strength prescription in the plan")
    routine = session.get("routine")
    if session.get("workout") != session_kinds.STRENGTH or not routine:
        raise ValueError(f"strength session without a routine: {session.get('id')}")
    return {
        "session": routine,
        "date": today,
        "sid": f"{session.get('id', routine)}-{today}",
        **spec,
    }
