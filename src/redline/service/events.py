"""Structured operational events: one JSON line per step of a turn.

They answer the questions a narrative line left open (which message, which route, how
long per step, why nothing went out) without ever carrying message text or health
data: identifiers, durations, causes.

    journalctl -u coach-messenger -o cat | jq 'select(.turn_id == "…")'
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger("redline.service.events")

#: Allowed fields. An unknown field is refused rather than logged: this is the only
#: way to guarantee that no message text ever slips in here.
FIELDS = frozenset({
    "turn_id", "inbound_ids", "job_id", "kind", "stage", "route", "model", "effort",
    "duration_ms", "outcome", "failure", "commands_applied", "command_kinds",
    "delivery", "fragments", "retry_in_s", "attempt", "depth", "plan", "cause",
    "restored", "runtime_tree", "age_s", "model_calls",
    # Shape of the envelope: counters, never the text.
    "prose_chars", "prose_bubbles", "added_chars", "added_parts", "questions",
})


def emit(event: str, **fields: Any) -> None:
    unknown = set(fields) - FIELDS
    if unknown:
        raise ValueError(f"event field not allowed: {', '.join(sorted(unknown))}")
    payload = {"event": event, **{key: value for key, value in fields.items()
                                  if value is not None}}
    log.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


class Stopwatch:
    """Duration of a step in milliseconds, measured on the monotonic clock."""

    def __init__(self) -> None:
        self._started = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)
