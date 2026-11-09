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
    redline descent --dplus 175.3 --altitude '[262.9, ..., 262.6]'
    redline strava streams <id> --keys altitude \
        | redline descent --dplus 175.3

The stream is passed as is: either the JSON returned by `redline strava streams`
({"altitude": [...]}), or the bare list.
"""

from __future__ import annotations


def descent(dplus: float, altitude: list) -> int:
    """The D- in metres, rounded. Flat terrain returns 0, never nothing."""
    if len(altitude) < 2:
        raise ValueError("elevation stream too short: two points at least")
    return max(0, round(dplus + altitude[0] - altitude[-1]))
