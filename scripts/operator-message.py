#!/usr/bin/env python3
"""Met en file un message écrit hors du service, livré tel quel sur Messenger.

Commande opérateur seulement (hors de `server/agent-cli-tools.txt`) : elle sert quand un
bilan ou une réponse a été rédigé ailleurs, depuis le Mac par exemple, et doit arriver à
L'athlète par la chaîne normale — silence de 8 min, trace en mémoire de conversation et en
messages proactifs — sans tour de modèle. La clé rend l'envoi idempotent : la même clé
ne part qu'une fois.

    scripts/operator-message.py --key bilan-w39 --text "…"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "server") not in sys.path:
    sys.path.insert(0, str(ROOT / "server"))

from redline.postgres_state import PostgresStateStore  # noqa: E402
from scripts.storage import Database  # noqa: E402

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
        raise SystemExit("DATABASE_URL obligatoire")
    state = PostgresStateStore(Database(url, application_name="operator-message"))
    now = time.time()
    job_key = f"operator:{args.key}"
    queued = state.proactive.enqueue_job(
        job_key, "operator_message", json.dumps({"text": text}, ensure_ascii=False), now,
        expire_at=now + VALID_HOURS * 3600, priority=True)
    print(f"{job_key} : {'mis en file' if queued else 'déjà en file ou envoyé, rien fait'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
