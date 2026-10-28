"""Événements d'exploitation structurés : une ligne JSON par étape d'un tour.

Ils répondent aux questions qu'une ligne narrative laissait ouvertes (quel message,
quelle route, combien de temps par étape, pourquoi rien n'est parti) sans jamais
porter le texte d'un message ni une donnée de santé : identifiants, durées, causes.

    journalctl -u coach-messenger -o cat | jq 'select(.turn_id == "…")'
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger("redline.service.events")

#: Champs autorisés. Un champ inconnu est refusé plutôt que journalisé : c'est la
#: seule façon de garantir qu'aucun texte de message ne se glisse un jour ici.
FIELDS = frozenset({
    "turn_id", "inbound_ids", "job_id", "kind", "stage", "route", "model", "effort",
    "duration_ms", "outcome", "failure", "commands_applied", "command_kinds",
    "delivery", "fragments", "retry_in_s", "attempt", "depth", "plan", "cause",
    "restored", "runtime_tree", "age_s", "model_calls",
    # Forme de l'enveloppe : des compteurs, jamais le texte.
    "prose_chars", "prose_bubbles", "added_chars", "added_parts", "questions",
})


def emit(event: str, **fields: Any) -> None:
    unknown = set(fields) - FIELDS
    if unknown:
        raise ValueError(f"champ d'événement non autorisé : {', '.join(sorted(unknown))}")
    payload = {"event": event, **{key: value for key, value in fields.items()
                                  if value is not None}}
    log.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


class Stopwatch:
    """Durée d'une étape en millisecondes, mesurée sur l'horloge monotone."""

    def __init__(self) -> None:
        self._started = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)
