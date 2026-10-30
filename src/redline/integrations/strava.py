#!/usr/bin/env python3
"""Strava access for the coach, whatever agent runs it.

Usage:
    redline strava check
    redline strava activity 90000000002
    redline strava laps 90000000002
    redline strava activities --limit 5
    redline strava streams 90000000002 --keys altitude | \
        redline descent --dplus 64.6

Output is a readable `key : value` block, or JSON with `--json`. `streams` always
prints flat JSON (`{"altitude": [...]}`), the shape `redline descent` reads.
Activities are parsed into `redline.model.activity.Activity`: cadence is in steps per
minute on foot (Strava counts one foot) and in revolutions otherwise, and values are
not rounded beyond the provider's precision.

Authentication has two owners. The service environment holds the application identity
(`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`) and nothing else. `$REDLINE_STATE_DIR/strava-token.json`
(0600, outside Git) holds the tokens, which rotate: access expires every 6 h and is
refreshed here, and Strava may replace the refresh token at that moment. The first
tokens are written once, on the machine that runs the coach, by

    redline strava login

which reads the authorisation code from standard input, never from an argument, so
that it does not land in the shell history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from redline.model import paths, session_kinds
from redline.model.activity import Activity, Lap, lap_summaries, summary
from redline.model.errors import ExternalError

API = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
TIMEOUT = 30

#: What `activities` shows of each activity: enough to pick one.
LIST_KEYS = ("id", "name", "sport", "start_local", "distance_km", "moving_time",
             "elevation_gain_m")

# Safety margin before expiry: a token valid for 40 s would not survive the call
# about to be made.
EXPIRY_MARGIN = 120


class StravaError(ExternalError):
    """A Strava access failure, stated plainly and never echoing a secret."""


class StravaNotFound(StravaError):
    """A 404: retrying will not bring back a deleted or private activity."""


# --------------------------------------------------------------------- tokens

def _cache_path() -> Path:
    override = (os.environ.get("STRAVA_TOKEN_CACHE") or "").strip()
    return Path(override) if override else paths.state_dir() / "strava-token.json"


def _read_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable cache is not a failure: it is rebuilt on refresh.
        return {}


def _write_cache(path: Path, payload: dict) -> None:
    """Atomic, private write: this file holds a refresh token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            raise StravaError(
                "Strava refuses the refresh token (authorization revoked or wrong "
                "credentials). Authorize the application again: no call will go through "
                "until then."
            ) from exc
        raise StravaError(f"Strava refused the token refresh (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise StravaError(f"Strava unreachable during the refresh: {exc.reason}") from exc


def _app_identity() -> tuple[str, str]:
    """The application identity, the only Strava fact held by the environment."""
    client_id = (os.environ.get("STRAVA_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("STRAVA_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise StravaError(
            "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET are missing from the environment: "
            "set them there (see config/redline.env.example)."
        )
    return client_id, client_secret


def access_token(*, force_refresh: bool = False) -> str:
    """A valid access token, refreshed and persisted as needed.

    Two facts, two owners, no overlap. The environment holds the application identity
    (client id and secret), which never rotates by itself. The cache holds the tokens,
    which rotate on every refresh. Keeping the refresh token in both places would leave
    a stale copy after the first rotation, so it lives in the cache only.

    Without a cache there is no fallback: bootstrapping is the explicit
    `redline strava login` step, and the error message names it.
    """
    client_id, client_secret = _app_identity()

    path = _cache_path()
    cache = _read_cache(path)
    if not force_refresh:
        token = cache.get("access_token")
        expires_at = cache.get("expires_at", 0)
        if token and expires_at - EXPIRY_MARGIN > time.time():
            return str(token)

    refresh_token = str(cache.get("refresh_token") or "")
    if not refresh_token:
        raise StravaError(
            f"no token in {path}: authorization was never done on this machine, or "
            "the cache was cleared. Run `redline strava login` ON THE MACHINE THAT "
            "RUNS THE COACH, and follow the two steps."
        )

    fresh = _refresh(client_id, client_secret, refresh_token)
    _write_cache(path, {
        "access_token": fresh["access_token"],
        "refresh_token": fresh.get("refresh_token", refresh_token),
        "expires_at": fresh.get("expires_at", int(time.time()) + 21600),
    })
    return str(fresh["access_token"])


# ---------------------------------------------------------------------- calls

def call(endpoint: str, params: dict | None = None) -> Any:
    """An authenticated GET, with a single token refresh on a 401."""
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{API}{endpoint}{query}"

    for attempt in (1, 2):
        token = access_token(force_refresh=attempt == 2)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 1:
                continue  # token stale on Strava's side: refresh and retry ONCE
            if exc.code == 404:
                raise StravaNotFound(
                    f"Strava does not know {endpoint}: activity deleted, private, "
                    "or wrong id."
                ) from exc
            if exc.code == 429:
                raise StravaError(
                    "Strava quota reached (200 requests / 15 min). Try again later; "
                    "do not conclude that the run does not exist."
                ) from exc
            raise StravaError(f"Strava refused the call {endpoint} (HTTP {exc.code})") from exc
        except urllib.error.URLError as exc:
            raise StravaError(f"Strava unreachable: {exc.reason}") from exc
    raise StravaError("Strava refused the token twice in a row")


# ------------------------------------------------------------------ reading

def _wall_clock(value: str | None) -> str | None:
    """`start_date_local` is a wall-clock time, but Strava serialises it with a `Z`."""
    if not value:
        return None
    return value.removesuffix("Z")


def _cadence(raw: float | None, on_foot: bool) -> dict[str, float]:
    """Strava counts one foot's steps on foot and crank turns otherwise."""
    if raw is None:
        return {}
    if on_foot:
        return {"cadence_spm": round(raw * 2, 1)}
    return {"cadence_rpm": round(raw, 1)}


def parse_lap(raw: dict, on_foot: bool) -> Lap:
    return Lap(
        name=raw.get("name"),
        distance_m=raw.get("distance") or 0.0,
        moving_s=raw.get("moving_time") or 0,
        hr_avg_bpm=raw.get("average_heartrate"),
        hr_max_bpm=raw.get("max_heartrate"),
        elevation_gain_m=raw.get("total_elevation_gain"),
        **_cadence(raw.get("average_cadence"), on_foot),
    )


def parse_activity(raw: dict) -> Activity:
    """An activity from `/activities/{id}` (with its laps) or a summary row of a list."""
    sport = raw.get("sport_type") or raw.get("type") or ""
    on_foot = sport in session_kinds.FOOT_SPORTS
    outdoor = not raw.get("trainer")
    return Activity(
        id=raw["id"],
        sport=sport,
        name=raw.get("name"),
        description=raw.get("description") or "",
        start_local=_wall_clock(raw.get("start_date_local")),
        distance_m=raw.get("distance") or 0.0,
        moving_s=raw.get("moving_time") or 0,
        elapsed_s=raw.get("elapsed_time"),
        elevation_gain_m=raw.get("total_elevation_gain"),
        hr_avg_bpm=raw.get("average_heartrate"),
        hr_max_bpm=raw.get("max_heartrate"),
        relative_effort=raw.get("suffer_score"),
        calories=raw.get("calories"),
        gear_id=raw.get("gear_id"),
        athlete_count=raw.get("athlete_count"),
        route_polyline=((raw.get("map") or {}).get("summary_polyline") or None
                        if outdoor else None),
        laps=tuple(parse_lap(lap, on_foot) for lap in raw.get("laps") or ()),
        **_cadence(raw.get("average_cadence"), on_foot),
    )


def distill_gear(raw: dict) -> dict:
    """Lifetime counter of a pair, expressed once in kilometres."""
    distance = raw.get("distance")
    if distance is None:
        raise StravaError("Strava returns no distance for this pair")
    return {
        "gear_id": raw.get("id"),
        "name": raw.get("name") or raw.get("nickname"),
        "distance_km": round(float(distance) / 1000, 1),
        "primary": bool(raw.get("primary")),
        "retired": bool(raw.get("retired")),
        "brand": raw.get("brand_name"),
        "model": raw.get("model_name"),
        "description": raw.get("description"),
    }


# ------------------------------------------------------------------ rendering

def _render(data: Any, indent: int = 0) -> list[str]:
    """A readable `key: value` block, cheaper in context than indented JSON."""
    pad = "  " * indent
    lines = []
    if isinstance(data, dict):
        for key, value in data.items():
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key} :")
                lines.extend(_render(value, indent + 1))
            else:
                lines.append(f"{pad}{key} : {value}")
    elif isinstance(data, list):
        for item in data:
            lines.extend(_render(item, indent))
            lines.append("")
    else:
        lines.append(f"{pad}{data}")
    return lines


def emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print("\n".join(line for line in _render(data)).strip())


# ------------------------------------------------------------------ commands

def cmd_activity(args) -> Any:
    """The summary, plus what `redline weather` needs for an outdoor activity."""
    activity = parse_activity(call(f"/activities/{args.id}"))
    out = summary(activity)
    if activity.weather_query:
        out["weather_query"] = activity.weather_query
    return out


def cmd_laps(args) -> Any:
    raw = call(f"/activities/{args.id}")
    if raw.get("laps") is None:
        # Only an absent field calls the endpoint: an empty list is an answer.
        raw = {**raw, "laps": call(f"/activities/{args.id}/laps")}
    activity = parse_activity(raw)
    return {"name": activity.name, "sport": activity.sport,
            "laps": lap_summaries(activity)}


def cmd_activities(args) -> Any:
    params: dict[str, Any] = {"per_page": args.limit}
    if args.after:
        params["after"] = int(time.mktime(time.strptime(args.after, "%Y-%m-%d")))
    return [{key: row[key] for key in LIST_KEYS if key in row}
            for row in (summary(parse_activity(raw)) for raw in call(
                "/athlete/activities", params))]


def cmd_streams(args) -> Any:
    """The streams, flattened to the shape `redline descent` already accepts as is."""
    keys = args.keys
    raw = call(f"/activities/{args.id}/streams", {"keys": keys, "key_by_type": "true"})
    return {name: stream.get("data") for name, stream in raw.items()
            if isinstance(stream, dict)}


def cmd_zones(args) -> Any:
    return call("/athlete/zones")


def gear_rows() -> list[dict]:
    athlete = call("/athlete")
    summaries = athlete.get("shoes") or []
    return [distill_gear(call(f"/gear/{row['id']}"))
            for row in summaries if row.get("id")]


def cmd_gear(args) -> Any:
    return gear_rows()


def cmd_login(args) -> Any:
    """Bootstrap the authorisation: the ONLY step that writes a token by hand.

    The authorisation code is read from standard input, never from an argument: a
    secret passed as an argument lands in the shell history.
    """
    client_id, client_secret = _app_identity()
    authorize = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": "http://localhost/exchange_token",
        "response_type": "code",
        "approval_prompt": "force",
        "scope": "activity:read_all",
    })
    print("1. Open this address in your browser and authorize the application:\n")
    print("   " + authorize + "\n")
    print("2. You will land on a `localhost` error page: that is expected, there is")
    print("   no server there. Copy the URL from the address bar, paste it here and")
    print("   press Enter.\n")
    try:
        pasted = input("Redirect URL (or the code alone): ").strip()
    except EOFError:
        raise StravaError("nothing received on standard input: run it again in an "
                          "interactive terminal, the command needs to read you.") from None

    code = pasted
    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
        values = urllib.parse.parse_qs(query).get("code")
        if not values:
            raise StravaError("no `code` parameter in this URL")
        code = values[0]
    if not code:
        raise StravaError("empty authorization code")

    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            fresh = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise StravaError(
            f"Strava refuses this code (HTTP {exc.code}). An authorization code works "
            "ONCE and expires fast: redo step 1 to get a new one."
        ) from exc
    except urllib.error.URLError as exc:
        raise StravaError(f"Strava unreachable: {exc.reason}") from exc

    path = _cache_path()
    _write_cache(path, {
        "access_token": fresh["access_token"],
        "refresh_token": fresh["refresh_token"],
        "expires_at": fresh.get("expires_at", int(time.time()) + 21600),
    })
    athlete = fresh.get("athlete") or {}
    return {
        "authorization": "recorded",
        "athlete": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "tokens_written_to": str(path),
        "reminder": "the environment must hold ONLY STRAVA_CLIENT_ID and "
                    "STRAVA_CLIENT_SECRET: the tokens live here and nowhere else.",
    }


def cmd_check(args) -> Any:
    """Proof that access is alive: run it before claiming it is not."""
    athlete = call("/athlete")
    cache = _read_cache(_cache_path())
    remaining = int(cache.get("expires_at", 0) - time.time())
    return {
        "access": "ok",
        "athlete": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "athlete_id": athlete.get("id"),
        "token_valid_for_min": max(0, remaining // 60),
    }


COMMANDS = {
    "activity": cmd_activity,
    "laps": cmd_laps,
    "activities": cmd_activities,
    "streams": cmd_streams,
    "zones": cmd_zones,
    "gear": cmd_gear,
    "check": cmd_check,
    "login": cmd_login,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="RAW values: redline ledger-add does the rounding, and only it.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine output")

    # `--json` is accepted both BEFORE and AFTER the subcommand. A caller naturally
    # writes `redline strava activity 42 --json`, which argparse only accepts up front
    # without this parent, and its error does not say where the flag belongs.
    # SUPPRESS makes both orders compatible: without it, the subcommand's default
    # would overwrite a `--json` already set up front.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine output")

    subparsers = parser.add_subparsers(dest="command", required=True)

    activity = subparsers.add_parser("activity", parents=[common],
                                     help="the distilled detail of an activity")
    activity.add_argument("id")

    laps = subparsers.add_parser("laps", parents=[common],
                                 help="each repetition, one by one")
    laps.add_argument("id")

    activities = subparsers.add_parser("activities", parents=[common],
                                       help="the recent activities")
    activities.add_argument("--limit", type=int, default=10)
    activities.add_argument("--after", help="YYYY-MM-DD")

    streams = subparsers.add_parser("streams", parents=[common],
                                    help="the streams, ready for redline descent")
    streams.add_argument("id")
    streams.add_argument("--keys", default="altitude",
                         help="altitude, heartrate, cadence, velocity_smooth, distance, time")

    subparsers.add_parser("zones", parents=[common],
                          help="the athlete's HR zones")
    subparsers.add_parser("gear", parents=[common],
                          help="the shoes and their fresh Strava distance")
    subparsers.add_parser("check", parents=[common],
                          help="check that Strava access works")
    subparsers.add_parser("login", parents=[common],
                          help="authorize the application and write the tokens (once)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        emit(COMMANDS[args.command](args), args.json or args.command == "streams")
    except StravaError as exc:
        print(f"Strava unavailable: {exc}", file=sys.stderr)
        return 1
    return 0

