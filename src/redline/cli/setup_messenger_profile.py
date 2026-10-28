#!/usr/bin/env python3
"""Configure the Page's Messenger profile (one-shot, rerun if the menu changes).

Installs the persistent menu (the hamburger in the conversation) and the
ice-breakers (suggestions shown on an empty conversation). Each button carries in
its payload the SENTENCE injected verbatim into the conversation, as if the athlete had
typed it: the webhook (redline/web.py) routes it to the coach with no lookup table.

The button labels and payloads stay in French (the athlete reads them); the code
is in English.

Usage (sur le VPS, environnement du service renseigné) :
  python -m redline.cli.setup_messenger_profile
"""

import os
import sys
from pathlib import Path

import requests

from redline.model import paths
from redline.service.config import _load_dotenv

MENU = [
    {"type": "postback", "title": "Le point sur ma semaine",
     "payload": "Fais-moi le point sur ma semaine en cours"},
    {"type": "postback", "title": "Le programme du jour",
     "payload": "C'est quoi le programme du jour ?"},
    {"type": "postback", "title": "Mon carnet Redline",
     "payload": "/carnet"},
]

ICE_BREAKERS = [
    {"question": "C'est quoi ma séance aujourd'hui ?",
     "payload": "C'est quoi ma séance aujourd'hui ?"},
    {"question": "Fais le point sur ma semaine",
     "payload": "Fais-moi le point sur ma semaine en cours"},
    {"question": "Où en est ma prépa TargetRace ?",
     "payload": "Où en est ma prépa TargetRace ?"},
    {"question": "Comment va ma blessure ?",
     "payload": "Fais le point sur ma blessure"},
]


def main() -> None:
    external_env = Path("/etc/redline/runtime/coach-messenger.env")
    _load_dotenv(external_env if external_env.is_file() else paths.ENGINE_ROOT / ".env")
    token = os.environ.get("MESSENGER_PAGE_TOKEN")
    if not token:
        sys.exit("MESSENGER_PAGE_TOKEN absent de l'environnement du service.")
    version = os.environ.get("GRAPH_API_VERSION", "v21.0")

    response = requests.post(
        f"https://graph.facebook.com/{version}/me/messenger_profile",
        headers={"Authorization": f"Bearer {token}"},
        json={
            # Meta requires a Get Started button before a persistent menu is
            # allowed. Its payload is routed like a typed message (redline/web.py).
            "get_started": {"payload": "/start"},
            "persistent_menu": [{
                "locale": "default",
                "composer_input_disabled": False,
                "call_to_actions": MENU,
            }],
            "ice_breakers": [{"locale": "default", "call_to_actions": ICE_BREAKERS}],
        },
        timeout=30,
    )
    if response.status_code >= 400:
        sys.exit(f"Failed ({response.status_code}): {response.text}")
    print("Messenger profile configured: persistent menu + ice-breakers. "
          "(Close/reopen the conversation to see the menu.)")


if __name__ == "__main__":
    main()
