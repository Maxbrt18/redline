"""How a recorded stream is read, for every reader of the Strava capture's streams.

A stream is read sample by sample: sample `k` covers the interval that precedes it. An
interval longer than `GAP_S` seconds is a stop (the watch paused); a sample below
`MOVING_MPS` does not count toward moving time. Strava's running cadence counts one leg:
a sample below `WALK_CADENCE_PER_LEG` is walked, and a stretch is walked when at least
`WALKED_SHARE` of its moving time is.
"""

from __future__ import annotations

GAP_S = 10
MOVING_MPS = 0.8
WALK_CADENCE_PER_LEG = 70
WALKED_SHARE = 0.5
