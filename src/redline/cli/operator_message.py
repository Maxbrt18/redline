#!/usr/bin/env python3
"""Queues a message written outside the service, delivered as is on Messenger.

Operator command only (kept out of `deploy/agent-cli-tools.txt`): it serves when a
review or a reply was drafted elsewhere, from a laptop for instance, and must reach
the athlete through the normal chain -- 8 min of silence, a trace in conversation memory
and in proactive messages -- without a model turn. The key makes the send idempotent:
the same key goes out only once.

    redline operator-message --key bilan-w39 --text "…"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time

from redline.model import entrypoint
from redline.model.errors import ConfigError
from redline.service.postgres_state import PostgresStateStore
from redline.storage import Database

KEY = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")
VALID_HOURS = 6.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, help="identité de l'envoi, ex. bilan-w39")
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    if not KEY.fullmatch(args.key):
        parser.error("--key : minuscules, chiffres et tirets, 3 à 61 caractères")
    text = args.text.strip()
    if not text:
        parser.error("--text vide")
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise ConfigError("DATABASE_URL obligatoire")
    state = PostgresStateStore(Database(url, application_name="operator-message"))
    now = time.time()
    job_key = f"operator:{args.key}"
    queued = state.proactive.enqueue_job(
        job_key, "operator_message", json.dumps({"text": text}, ensure_ascii=False), now,
        expire_at=now + VALID_HOURS * 3600, priority=True)
    print(f"{job_key} : {'mis en file' if queued else 'déjà en file ou envoyé, rien fait'}")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
