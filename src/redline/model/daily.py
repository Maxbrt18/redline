"""The day's state aggregated for the gauge (PostgreSQL `daily_checkins`).

Sleep and life load are two **gauge multipliers** (`coaching/protocols.md`) and two
documented co-factors of the injuries. They are recorded per day so that the coach
knows how the athlete slept during the week, and the "2-3 bad nights" guard-rail can
trigger without waiting for a weekly entry.

**Design constraint**: this data stays **declarative and conversational**. Sleep feeling,
life load and desire stay three words, not three scores, and are never rendered as a
curve.

**An empty cell is legitimate**: the athlete does not answer every day, and a guessed
value would be worse than an absence (same rule as the ledger).
"""

from __future__ import annotations

# ------------------------------------------------------------------ aggregation
#
# The thresholds below are the only numbers in this module that arbitrate (the gauge).
# They are named here rather than buried in a comprehension, so they can be challenged.

# Guard-rail: 2-3 consecutive bad nights mean the week is treated as an absorption week.
# The gauge vocabulary already distinguishes these two levels; this only feeds them.
BAD_NIGHTS_FOR_ALERT = 2
BAD_NIGHTS_FOR_WEEK = 4

# A heavy day is not a heavy week. The multiplier targets the stress bucket of a PERIOD,
# not a single hard day.
HEAVY_DAYS_FOR_ELEVEE = 3


def sleep_of(days: list) -> str | None:
    """The gauge `sommeil` value for this week, or None if nothing is declared.

    None and not "ok": a week without declaration is not a good week, and reporting "ok"
    would silence a multiplier by default.
    """
    declared = [day["sommeil"] for day in days if "sommeil" in day]
    if not declared:
        return None
    bad = declared.count("mauvaise")
    if bad >= BAD_NIGHTS_FOR_WEEK:
        return "degrade_semaine"
    if bad >= BAD_NIGHTS_FOR_ALERT:
        return "2_3_mauvaises_nuits"
    return "ok"


def life_load_of(days: list) -> str | None:
    declared = [day["charge_vie"] for day in days if "charge_vie" in day]
    if not declared:
        return None
    return "elevee" if declared.count("elevee") >= HEAVY_DAYS_FOR_ELEVEE else "normale"
