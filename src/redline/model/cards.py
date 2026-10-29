"""What each card script accepts on stdin, declared instead of described.

These contracts used to live in the docstring of every `chart-*.py`: 58 lines of
prose that nothing enforced, so a key Claude mistyped in the morning produced a
wrong card or a KeyError rather than a refusal naming the field.

The strength card is absent on purpose: its input is a SELECTION of the program,
already declared as `SELECTION_SCHEMA` in `program.py` next to what it selects.

WHAT IS BOUNDED HERE, AND WHAT IS NOT. These schemas constrain MEANING: a scale
that runs 0-10, a distance that cannot be zero because a pace divides by it, the
two reps a drift needs to exist. They do NOT cap the length of a display string --
that is `render.py`'s layout guard, which measures the laid-out page in the browser
and refuses a card that overflows. A `maximum=30` on a title is a guess competing
with a measurement, and the measurement wins.
"""

from __future__ import annotations

from .schema import Schema, field

DATE = r"\d{4}-\d{2}-\d{2}"
STATUS = ("ok", "warn", "bad")

DAY_SCHEMA = Schema("day", [
    field("day", str, note="the column label, e.g. Mon"),
    field("planned_km", float, required=False, minimum=0),
    field("done_km", float, required=False, minimum=0),
    field("quality", bool, required=False, note="marks a demanding session day"),
])

WEEK_SCHEMA = Schema("week", [
    field("period", str, required=False, pattern=r"20\d{2}-W\d{2}", note="the review's canonical period, e.g. 2030-W39"),
    field("week", str, note="the short title, e.g. W30"),
    field("dates", str, required=False),
    field("days", list, minimum=1, maximum=7, item=field("day", dict, schema=DAY_SCHEMA),
          note="Monday to Sunday"),
    field("planned_km", float, required=False, minimum=0),
    field("done_km", float, required=False, minimum=0),
    field("gauge_level", float, required=False, minimum=0,
          note="the load gauge, cap 85 (coaching/protocols.md)"),
    field("gauge_ratio", float, required=False, minimum=0, note="the acute/chronic ratio"),
    field("pain", str, required=False, note="pain trend, free text"),
    field("pain_flag", str, required=False, choices=STATUS),
    field("enjoyment", float, required=False, minimum=0, maximum=10),
    field("cmj_cm", float, required=False, minimum=0),
])

WEEK_TOTAL_SCHEMA = Schema("week of the month", [
    field("label", str),
    field("km", float, required=False, minimum=0),
])

MONTH_SCHEMA = Schema("month", [
    field("period", str, required=False, pattern=r"20\d{2}-\d{2}", note="the review's canonical period, e.g. 2030-09"),
    field("month", str, note="the title, e.g. July 2030"),
    field("km", float, required=False, minimum=0),
    field("dplus", float, required=False, minimum=0),
    field("sessions", int, required=False, minimum=0),
    field("weeks", list, required=False, maximum=6,
          item=field("week", dict, schema=WEEK_TOTAL_SCHEMA)),
    # Free-form keys: they are the capability LABELS the cockpit prints, and they
    # change with the cockpit, not with the code.
    field("capabilities", dict, required=False,
          note="capability name -> score, see redline fact-read capabilities"),
    field("cap_scale", float, required=False, minimum=1, default=5),
    field("race_name", str, required=False),
    field("race_date", str, required=False, pattern=DATE),
])

RECOVERY_SCHEMA = Schema("recovery", [
    field("time_s", float, minimum=1),
    # Zero IS allowed here: a standing recovery covers no ground.
    field("dist_m", float, required=False, minimum=0, default=0),
])

REP_SCHEMA = Schema("repetition", [
    field("label", str, required=False),
    field("time_s", float, minimum=1),
    # Not `minimum=0`: a zero distance is a division by zero two lines later, and
    # "allure infinie" is not a card, it is a traceback.
    field("dist_m", float, minimum=1),
    field("hr", float, required=False, minimum=1),
    field("recovery", dict, required=False, schema=RECOVERY_SCHEMA,
          note="the recovery that FOLLOWS this rep, read from its Strava lap"),
])

WORKOUT_SCHEMA = Schema("quality session", [
    field("title", str, required=False),
    field("date", str, required=False),
    field("target_pace", object, required=False,
          note='"M:SS" in min/km, or a number of seconds per km'),
    field("note", str, required=False,
          note="what the target pace does not say (zone, recovery): it is already shown"),
    # Two reps minimum: the card reads a DRIFT between the first and the last, and
    # a single rep has no drift to read.
    field("reps", list, minimum=2, item=field("rep", dict, schema=REP_SCHEMA),
          note="in the order run"),
])
