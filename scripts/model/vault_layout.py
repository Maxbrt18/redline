"""Where the engine reads and writes inside a vault, relative to its root.

Every path the engine knows in a vault is named here once; modules join it to
`paths.vault_root()` (or the vault they are given) and never spell a folder themselves.
"""

from __future__ import annotations

from pathlib import Path

#: The coach's entry point, regenerated from the plan.
HOT_CACHE = Path("ai/hot-cache.md")

ATHLETE_PROFILE = Path("athlete/athlete.md")
#: One note per ISO week, holding the rendered plan.
JOURNAL = Path("athlete/journal")
#: Projections of PostgreSQL facts, never edited by hand.
DAILY_CSV = Path("athlete/daily.csv")
INSTRUMENTS_CSV = Path("athlete/instruments.csv")
STRENGTH_LOG_CSV = Path("athlete/strength-log.csv")
PROJECTIONS = (DAILY_CSV, INSTRUMENTS_CSV, STRENGTH_LOG_CSV)

DECISIONS = Path("coaching/decisions.md")
CORRECTIONS = Path("coaching/corrections.md")
CURRENT_PLAN = Path("coaching/current-plan.md")
PROTOCOLS = Path("coaching/protocols.md")
STRENGTH = Path("coaching/strength.md")
#: One note per kind of session or strength routine.
WORKOUTS = Path("coaching/workouts")
EXERCISES = Path("coaching/exercises")
#: The reasoning behind each tracked capability; its score lives in PostgreSQL.
CAPABILITIES = Path("coaching/capabilities")

#: The knowledge base the coach searches.
CONCEPTS = Path("concepts")
EPISODES = Path("episodes")


def journal(week: str) -> Path:
    """The journal note of an ISO week such as `2026-W45`."""
    return JOURNAL / f"{week.lower()}.md"
