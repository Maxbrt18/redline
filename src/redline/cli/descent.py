"""The `redline descent` command: the D- of an activity, from its elevation stream."""

from __future__ import annotations

import argparse
import json

from redline.analysis import descent as descent_model
from redline.model import agent_input, entrypoint
from redline.model.errors import InputError


def _altitude(payload: str) -> list:
    data = json.loads(payload)
    if isinstance(data, dict):
        data = data.get("altitude")
    if not isinstance(data, list):
        raise ValueError("elevation stream not found: expected a list, or an object "
                         "{\"altitude\": [...]} as returned by `redline strava streams`")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=descent_model.__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dplus", type=float, required=True,
                        help="the activity's elevation_gain, in metres (Strava)")
    parser.add_argument("--altitude", help="elevation stream as JSON (default: stdin)")
    parser.add_argument("--input", help="file of the elevation stream as JSON")
    args = parser.parse_args()

    try:
        altitude = _altitude(args.altitude or agent_input.read_text(args.input))
        print(descent_model.descent(args.dplus, altitude))
    except ValueError as exc:
        raise InputError(f"D- cannot be computed -- {exc}") from exc
    return 0


if __name__ == "__main__":
    entrypoint.run(main)
