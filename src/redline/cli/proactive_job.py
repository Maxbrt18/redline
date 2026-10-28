#!/usr/bin/env python3
"""Inspecte ou rouvre un bilan périodique exact après un incident d'exploitation.

Commande opérateur seulement : elle reste hors de `deploy/agent-cli-tools.txt`, donc
le coach en sandbox ne peut pas rejouer lui-même une initiative déjà acquittée.
"""

from __future__ import annotations

import argparse
import os
import re
import time

from redline.model import entrypoint
from redline.model.errors import ConfigError, InputError
from redline.service.postgres_state import PostgresStateStore
from redline.storage import Database

PERIODIC_KEY = re.compile(
    r"^((?:weekly|weekly-repair):20\d{2}-W(?:0[1-9]|[1-4]\d|5[0-3])|"
    r"monthly:20\d{2}-(?:0[1-9]|1[0-2]))$")


def _state() -> PostgresStateStore:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise ConfigError("DATABASE_URL obligatoire")
    return PostgresStateStore(Database(url, application_name="proactive-job"))


def _row(state: PostgresStateStore, key: str) -> dict:
    try:
        return next(job for job in state.proactive.jobs() if job["id"] == key)
    except StopIteration:
        raise InputError(f"job inconnu : {key}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "reopen"))
    parser.add_argument("--job-key", required=True)
    parser.add_argument("--valid-hours", type=float, default=24.0)
    args = parser.parse_args()
    if not PERIODIC_KEY.fullmatch(args.job_key):
        parser.error("--job-key doit nommer exactement weekly:YYYY-WNN, "
                     "weekly-repair:YYYY-WNN ou monthly:YYYY-MM")
    if not 0 < args.valid_hours <= 48:
        parser.error("--valid-hours doit être dans ]0, 48]")

    state = _state()
    before = _row(state, args.job_key)
    if args.command == "reopen":
        if before["status"] not in {"done", "failed"}:
            raise InputError(
                f"{args.job_key} : réouverture refusée, statut {before['status']}")
        now = time.time()
        state.proactive.reopen_job(
            args.job_key, due=now, expire_at=now + args.valid_hours * 3600)
    current = _row(state, args.job_key)
    cause = f" cause={current['last_error']}" if current.get("last_error") else ""
    print(
        f"{current['id']} : statut={current['status']} "
        f"tentatives={current['attempts']}{cause}")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
