"""Where the engine reads and writes inside a vault, relative to its root.

Every path the engine knows in a vault is named here once; modules join it to
`paths.vault_root()` (or the vault they are given) and never spell a folder themselves.
"""

from __future__ import annotations

from pathlib import Path

#: The coach's entry point, regenerated from the plan.
HOT_CACHE = Path("ai/hot-cache.md")

#: One note per ISO week, holding the rendered plan.
JOURNAL = Path("athlete/journal")
#: Projections of PostgreSQL facts, never edited by hand.
DAILY_CSV = Path("athlete/daily.csv")
INSTRUMENTS_CSV = Path("athlete/instruments.csv")
STRENGTH_LOG_CSV = Path("athlete/strength-log.csv")
#: The cycle version in force, as the coach reads it in the vault.
CURRENT_PLAN = Path("coaching/current-plan.md")
#: Where a hand-written `current-plan.md` is kept when the projection first replaces it.
CURRENT_PLAN_KEPT = Path("coaching/current-plan.written.md")
PROJECTIONS = (DAILY_CSV, INSTRUMENTS_CSV, STRENGTH_LOG_CSV, CURRENT_PLAN)

DECISIONS = Path("coaching/decisions.md")
CORRECTIONS = Path("coaching/corrections.md")
#: How each correction is replayed: a scenario of the bench, code, or nothing.
CORRECTIONS_REPLAY = Path("coaching/corrections-replay.json")
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
    """The journal note of an ISO week such as `2030-W45`."""
    return JOURNAL / f"{week.lower()}.md"
