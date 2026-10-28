#!/usr/bin/env python3
"""Strava webhook subscription: status and renewal, never printing a secret.

Strava accepts only one subscription per application and does not sign its events:
the callback URL `<COACH_BASE_URL>/strava-webhook/<STRAVA_WEBHOOK_SECRET>` is
therefore the route's only authentication. `renew` replaces the existing subscription
with that one; Strava validates the URL immediately through the `hub.*` handshake, so
the service must already be running with this secret. When nothing needs to change,
`renew` touches nothing.

Usage (on the VPS, with the service environment loaded):
  set -a; . /etc/redline/runtime/coach-messenger.env; set +a
  python3 deploy/strava-subscription.py status   # exit 0 if the subscription is correct
  python3 deploy/strava-subscription.py renew
"""

from __future__ import annotations

import os
import sys

import requests

API = "https://www.strava.com/api/v3/push_subscriptions"
TIMEOUT_S = 30


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} absent de l'environnement")
    return value


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"status", "renew"}:
        print(__doc__, file=sys.stderr)
        return 2
    client = {"client_id": _env("STRAVA_CLIENT_ID"),
              "client_secret": _env("STRAVA_CLIENT_SECRET")}
    path_secret = _env("STRAVA_WEBHOOK_SECRET")
    verify_token = _env("STRAVA_VERIFY_TOKEN")
    expected = f"{_env('COACH_BASE_URL').rstrip('/')}/strava-webhook/{path_secret}"
    hidden = (client["client_secret"], path_secret, verify_token)

    def fail(response: requests.Response) -> SystemExit:
        body = response.text[:300]
        for secret in hidden:
            body = body.replace(secret, "[secret]")
        return SystemExit(f"Strava HTTP {response.status_code} : {body}")

    response = requests.get(API, params=client, timeout=TIMEOUT_S)
    if not response.ok:
        raise fail(response)
    current = response.json()
    for item in current:
        verdict = "attendu" if item.get("callback_url") == expected else "DIFFÉRENT"
        print(f"abonnement {item['id']} : callback {verdict}")
    if not current:
        print("aucun abonnement")
    in_place = len(current) == 1 and current[0].get("callback_url") == expected
    if argv[1] == "status" or in_place:
        return 0 if in_place else 1

    for item in current:
        deleted = requests.delete(f"{API}/{item['id']}", params=client, timeout=TIMEOUT_S)
        if deleted.status_code != 204:
            raise fail(deleted)
        print(f"abonnement {item['id']} supprimé")
    created = requests.post(API, data={**client, "callback_url": expected,
                                       "verify_token": verify_token}, timeout=TIMEOUT_S)
    if not created.ok:
        raise fail(created)
    print(f"abonnement {created.json()['id']} créé")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
