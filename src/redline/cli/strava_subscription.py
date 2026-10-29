"""Strava webhook subscription: status and renewal, never printing a secret.

Strava accepts only one subscription per application and does not sign its events:
the callback URL `<COACH_BASE_URL>/strava-webhook/<STRAVA_WEBHOOK_SECRET>` is
therefore the route's only authentication. `renew` replaces the existing subscription
with that one; Strava validates the URL immediately through the `hub.*` handshake, so
the service must already be running with this secret. When nothing needs to change,
`renew` touches nothing.

Usage, with the service environment loaded:
  redline strava-subscription status   # exit 0 if the subscription is correct
  redline strava-subscription renew
"""

from __future__ import annotations

import argparse
import os

import requests

from redline.model import entrypoint
from redline.model.errors import ConfigError, ExternalError

API = "https://www.strava.com/api/v3/push_subscriptions"
TIMEOUT_S = 30


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} missing from the environment")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("status", "renew"))
    action = parser.parse_args().action
    client = {"client_id": _env("STRAVA_CLIENT_ID"),
              "client_secret": _env("STRAVA_CLIENT_SECRET")}
    path_secret = _env("STRAVA_WEBHOOK_SECRET")
    verify_token = _env("STRAVA_VERIFY_TOKEN")
    expected = f"{_env('COACH_BASE_URL').rstrip('/')}/strava-webhook/{path_secret}"
    hidden = (client["client_secret"], path_secret, verify_token)

    def fail(response: requests.Response) -> ExternalError:
        body = response.text[:300]
        for secret in hidden:
            body = body.replace(secret, "[secret]")
        return ExternalError(f"Strava HTTP {response.status_code}: {body}")

    response = requests.get(API, params=client, timeout=TIMEOUT_S)
    if not response.ok:
        raise fail(response)
    current = response.json()
    for item in current:
        verdict = "expected" if item.get("callback_url") == expected else "DIFFERENT"
        print(f"subscription {item["id"]}: callback {verdict}")
    if not current:
        print("no subscription")
    in_place = len(current) == 1 and current[0].get("callback_url") == expected
    if action == "status" or in_place:
        return 0 if in_place else 1

    for item in current:
        deleted = requests.delete(f"{API}/{item['id']}", params=client, timeout=TIMEOUT_S)
        if deleted.status_code != 204:
            raise fail(deleted)
        print(f"subscription {item["id"]} deleted")
    created = requests.post(API, data={**client, "callback_url": expected,
                                       "verify_token": verify_token}, timeout=TIMEOUT_S)
    if not created.ok:
        raise fail(created)
    print(f"subscription {created.json()["id"]} created")
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
