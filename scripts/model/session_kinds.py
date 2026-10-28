"""The kinds of session the engine plans and records: one slug each, one owner.

A plan prescribes a kind (`planned_sessions.workout`), the ledger records one
(`observations.kind`), and both use the same slug. Every set a module needs (the run
kinds, the kinds that ask for enjoyment, the demanding ones…) is derived here from the
declared attributes; no module lists slugs of its own.

Which strength routine a `strength` session follows is instance data: the routines are
the workout notes of the vault that declare `routine:` (see `programme.py`).
Athlete-facing labels live in `scripts/coach/fr/session_labels.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionKind:
    slug: str
    #: What the session is, physically: run, hike, bike, strength, test, rest, reading or
    #: measure. A plan's `kind` column is the family of its workout.
    family: str
    planned: bool
    recorded: bool
    #: Easy running: the reference for cadence and heart-rate drift.
    easy: bool = False
    #: Intensity that weighs on the load gauge.
    quality: bool = False
    #: Made of repetitions: the debrief reads its laps as reps.
    repetitions: bool = False
    #: Demanding by nature, whatever its duration.
    demanding: bool = False
    #: Long enough to rehearse race fuelling.
    fuelled: bool = False


KINDS = (
    SessionKind("easy", "run", planned=True, recorded=True, easy=True),
    SessionKind("long", "run", planned=True, recorded=True, easy=True, demanding=True,
                fuelled=True),
    SessionKind("threshold", "run", planned=True, recorded=True, quality=True,
                repetitions=True, demanding=True),
    SessionKind("hills", "run", planned=True, recorded=True, quality=True,
                repetitions=True, demanding=True),
    SessionKind("strides", "run", planned=True, recorded=True, repetitions=True),
    SessionKind("cadence", "run", planned=True, recorded=True),
    SessionKind("race", "run", planned=True, recorded=True, demanding=True, fuelled=True),
    SessionKind("hike", "hike", planned=True, recorded=True),
    SessionKind("bike", "bike", planned=True, recorded=True),
    SessionKind("strength", "strength", planned=True, recorded=True),
    SessionKind("test", "test", planned=True, recorded=False),
    SessionKind("rest", "rest", planned=True, recorded=False),
    SessionKind("wake-trace", "reading", planned=False, recorded=True),
    SessionKind("rest-reading", "reading", planned=False, recorded=True),
    SessionKind("instruments", "measure", planned=False, recorded=True),
    SessionKind("palpation", "measure", planned=False, recorded=True),
)
BY_SLUG = {kind.slug: kind for kind in KINDS}

EASY = "easy"
LONG = "long"
STRENGTH = "strength"
WAKE_TRACE = "wake-trace"

PLANNED = tuple(kind.slug for kind in KINDS if kind.planned)
RECORDED = tuple(kind.slug for kind in KINDS if kind.recorded)
RUN = frozenset(kind.slug for kind in KINDS if kind.family == "run")
WALK = frozenset(kind.slug for kind in KINDS if kind.family == "hike")
MOVING = frozenset(kind.slug for kind in KINDS if kind.family in {"run", "hike", "bike"})
#: Pain read at rest: never an effort, never a load.
READINGS = tuple(kind.slug for kind in KINDS if kind.family == "reading")
EFFORT = tuple(kind.slug for kind in KINDS
               if kind.recorded and kind.family in {"run", "hike", "bike", "strength"})
ENJOYMENT = tuple(kind.slug for kind in KINDS if kind.family in {"run", "hike"})
#: Counted at half their duration in the load gauge.
HALF_LOAD = frozenset(kind.slug for kind in KINDS
                      if kind.family in {"hike", "bike", "strength"})
EASY_RUNS = frozenset(kind.slug for kind in KINDS if kind.easy)
QUALITY = frozenset(kind.slug for kind in KINDS if kind.quality)
REPETITIONS = frozenset(kind.slug for kind in KINDS if kind.repetitions)
DEMANDING = frozenset(kind.slug for kind in KINDS if kind.demanding)
FUELLED = frozenset(kind.slug for kind in KINDS if kind.fuelled)

#: Share of the descent that counts in the load gauge: a run descent in full, a
#: walked one at half, none for anything that does not descend on foot.
DESCENT_WEIGHT_BY_FAMILY = {"run": 1.0, "hike": 0.5}

#: The Strava sports each family can match, for families that Strava records.
STRAVA_SPORTS = {
    "run": frozenset({"Run", "TrailRun", "VirtualRun"}),
    "hike": frozenset({"Walk", "Hike"}),
    "bike": frozenset({"Ride", "VirtualRide", "MountainBikeRide", "GravelRide"}),
}
#: Sports counted in steps and paced in min/km.
FOOT_SPORTS = STRAVA_SPORTS["run"] | STRAVA_SPORTS["hike"]


def family(slug: str) -> str:
    return BY_SLUG[slug].family


def descent_weight(slug: str) -> float:
    return DESCENT_WEIGHT_BY_FAMILY.get(BY_SLUG[slug].family, 0.0)


def planned_for_sport(sport: str) -> tuple[str, ...]:
    """The planned kinds a Strava activity of this sport can fulfil."""
    families = {name for name, sports in STRAVA_SPORTS.items() if sport in sports}
    return tuple(kind.slug for kind in KINDS if kind.planned and kind.family in families)
