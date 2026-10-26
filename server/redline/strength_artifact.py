"""Produit la carte de renforcement depuis la prescription typée du plan."""

from __future__ import annotations

from pathlib import Path

from scripts.cards import strength_card

from . import strength
from .outbox import Outbox

WORKOUT_SESSION = {"renfo-a": "a", "renfo-b": "b"}
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
        raise ValueError("prescription strength absente du plan")
    workout = session.get("workout")
    if workout not in WORKOUT_SESSION:
        raise ValueError(f"workout de renforcement inconnu : {workout}")
    return {
        "session": WORKOUT_SESSION[workout],
        "date": today,
        "sid": f"{session.get('id', workout)}-{today}",
        **spec,
    }
