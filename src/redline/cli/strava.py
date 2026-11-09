"""The `redline strava` command: see `redline.integrations.strava`."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
from typing import Any

from redline.integrations import strava
from redline.model import entrypoint
from redline.model.activity import lap_summaries, summary


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


def cmd_activity(args) -> Any:
    """The summary, plus what `redline weather` needs for an outdoor activity."""
    activity = strava.parse_activity(strava.call(f"/activities/{args.id}"))
    out = summary(activity)
    if activity.weather_query:
        out["weather_query"] = activity.weather_query
    return out


def cmd_laps(args) -> Any:
    raw = strava.call(f"/activities/{args.id}")
    if raw.get("laps") is None:
        # Only an absent field calls the endpoint: an empty list is an answer.
        raw = {**raw, "laps": strava.call(f"/activities/{args.id}/laps")}
    activity = strava.parse_activity(raw)
    return {"name": activity.name, "sport": activity.sport,
            "laps": lap_summaries(activity)}


def cmd_activities(args) -> Any:
    params: dict[str, Any] = {"per_page": args.limit}
    if args.after:
        params["after"] = int(time.mktime(time.strptime(args.after, "%Y-%m-%d")))
    return [{key: row[key] for key in strava.LIST_KEYS if key in row}
            for row in (summary(strava.parse_activity(raw)) for raw in strava.call(
                "/athlete/activities", params))]


def cmd_streams(args) -> Any:
    """The streams, flattened to the shape `redline descent` already accepts as is."""
    keys = args.keys
    raw = strava.call(f"/activities/{args.id}/streams", {"keys": keys, "key_by_type": "true"})
    return {name: stream.get("data") for name, stream in raw.items()
            if isinstance(stream, dict)}


def cmd_zones(args) -> Any:
    return strava.call("/athlete/zones")


def cmd_gear(args) -> Any:
    return strava.gear_rows()


def cmd_login(args) -> Any:
    """Bootstrap the authorisation: the ONLY step that writes a token by hand.

    The authorisation code is read from standard input, never from an argument: a
    secret passed as an argument lands in the shell history.
    """
    authorize = strava.authorize_url()
    print("1. Open this address in your browser and authorize the application:\n")
    print("   " + authorize + "\n")
    print("2. You will land on a `localhost` error page: that is expected, there is")
    print("   no server there. Copy the URL from the address bar, paste it here and")
    print("   press Enter.\n")
    try:
        pasted = input("Redirect URL (or the code alone): ").strip()
    except EOFError:
        raise strava.StravaError("nothing received on standard input: run it again in an "
                                 "interactive terminal, the command needs to read you.") from None

    code = pasted
    if "code=" in pasted:
        query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
        values = urllib.parse.parse_qs(query).get("code")
        if not values:
            raise strava.StravaError("no `code` parameter in this URL")
        code = values[0]
    if not code:
        raise strava.StravaError("empty authorization code")
    athlete, path = strava.exchange_code(code)
    return {
        "authorization": "recorded",
        "athlete": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "tokens_written_to": str(path),
        "reminder": "the environment must hold ONLY STRAVA_CLIENT_ID and "
                    "STRAVA_CLIENT_SECRET: the tokens live here and nowhere else.",
    }


def cmd_check(args) -> Any:
    """Proof that access is alive: run it before claiming it is not."""
    athlete = strava.call("/athlete")
    return {
        "access": "ok",
        "athlete": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "athlete_id": athlete.get("id"),
        "token_valid_for_min": strava.token_minutes_left(),
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
        description=strava.__doc__.splitlines()[0],
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
    except strava.StravaError as exc:
        raise strava.StravaError(f"Strava unavailable: {exc}") from exc
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
