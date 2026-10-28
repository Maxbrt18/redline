#!/usr/bin/env python3
"""The descent (D-) of an activity, which Strava does not return.

The API gives POSITIVE elevation gain (`elevation_gain`) and nothing for the negative side.
On hilly terrain descent is a load of its own (impact, braking), so the ledger's D- column
must be filled, not left empty.

Method: the balance identity, not a second estimate.

    D- = D+ + (start_altitude - end_altitude)

The figure is anchored on Strava's D+, hence on ITS smoothing, instead of redoing one with
our own smoothing that would not match the D+ column next to it. Only the two end points of
the altitude stream enter the computation.

Summing the positive deltas of the stream (resolution 100) lands within a few metres of
Strava's D+ on rolling runs but underestimates it by ~5 % on a big hike: the smoothing eats
the flats. The identity above does not have that flaw. On a descent where Strava had
reported D-, it gives 1101 for 1096 recorded (0.5 % off).

Usage:
    python3 scripts/descent.py --dplus 175.3 --altitude '[262.9, ..., 262.6]'
    python3 scripts/strava.py streams <id> --keys altitude \
        | python3 scripts/descent.py --dplus 175.3

The stream is passed as is: either the JSON returned by `strava.py streams`
({"altitude": [...]}), or the bare list.
"""

from __future__ import annotations

import argparse
import json

from redline.model import (
    agent_input,
)
from redline.model.errors import InputError


def descent(dplus: float, altitude: list) -> int:
    """The D- in metres, rounded. Flat terrain returns 0, never nothing."""
    if len(altitude) < 2:
        raise ValueError("flux d'altitude trop court : deux points au minimum")
    return max(0, round(dplus + altitude[0] - altitude[-1]))


def _altitude(payload: str) -> list:
    data = json.loads(payload)
    if isinstance(data, dict):
        data = data.get("altitude")
    if not isinstance(data, list):
        raise ValueError("flux d'altitude introuvable : attendu une liste, ou un objet "
                         "{\"altitude\": [...]} tel que renvoyé par `strava.py streams`")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dplus", type=float, required=True,
                        help="elevation_gain de l'activité, en mètres (Strava)")
    parser.add_argument("--altitude", help="flux d'altitude JSON (défaut : stdin)")
    parser.add_argument("--input", help="fichier du flux d'altitude JSON")
    args = parser.parse_args()

    try:
        altitude = _altitude(args.altitude or agent_input.read_text(args.input))
        print(descent(args.dplus, altitude))
    except ValueError as exc:
        raise InputError(f"D− incalculable — {exc}") from exc
    return 0

