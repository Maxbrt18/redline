"""What each card script accepts on stdin, declared instead of described.

These contracts used to live in the docstring of every `chart-*.py`: 58 lines of
prose that nothing enforced, so a key Claude mistyped in the morning produced a
wrong card or a KeyError rather than a refusal naming the field.

The strength card is absent on purpose: its input is a SELECTION of the programme,
already declared as `SELECTION_SCHEMA` in `programme.py` next to what it selects.

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

DAY_SCHEMA = Schema("jour", [
    field("day", str, note="l'étiquette de colonne, ex. lun"),
    field("planned_km", float, required=False, minimum=0),
    field("done_km", float, required=False, minimum=0),
    field("quality", bool, required=False, note="marque un jour de séance exigeante"),
])

WEEK_SCHEMA = Schema("semaine", [
    field("period", str, required=False, pattern=r"20\d{2}-W\d{2}", note="période canonique du bilan, ex. 2030-W39"),
    field("week", str, note="le titre court, ex. W30"),
    field("dates", str, required=False),
    field("days", list, minimum=1, maximum=7, item=field("jour", dict, schema=DAY_SCHEMA),
          note="du lundi au dimanche"),
    field("planned_km", float, required=False, minimum=0),
    field("done_km", float, required=False, minimum=0),
    field("gauge_level", float, required=False, minimum=0,
          note="la jauge de charge, plafond 85 (coaching/protocols.md)"),
    field("gauge_ratio", float, required=False, minimum=0, note="le ratio aigu/chronique"),
    field("pain", str, required=False, note="tendance de la douleur, texte libre"),
    field("pain_flag", str, required=False, choices=STATUS),
    field("plaisir", float, required=False, minimum=0, maximum=10),
    field("cmj_cm", float, required=False, minimum=0),
])

WEEK_TOTAL_SCHEMA = Schema("semaine du mois", [
    field("label", str),
    field("km", float, required=False, minimum=0),
])

MONTH_SCHEMA = Schema("mois", [
    field("period", str, required=False, pattern=r"20\d{2}-\d{2}", note="période canonique du bilan, ex. 2030-09"),
    field("month", str, note="le titre, ex. Juillet 2030"),
    field("km", float, required=False, minimum=0),
    field("dplus", float, required=False, minimum=0),
    field("sessions", int, required=False, minimum=0),
    field("weeks", list, required=False, maximum=6,
          item=field("semaine", dict, schema=WEEK_TOTAL_SCHEMA)),
    # Free-form keys: they are the capability LABELS the cockpit prints, and they
    # change with the cockpit, not with the code.
    field("capabilities", dict, required=False,
          note="nom de capacité -> score, cf. scripts/fact-read.py capabilities"),
    field("cap_scale", float, required=False, minimum=1, default=5),
    field("race_name", str, required=False),
    field("race_date", str, required=False, pattern=DATE),
])

RECOVERY_SCHEMA = Schema("récupération", [
    field("time_s", float, minimum=1),
    # Zero IS allowed here: a standing recovery covers no ground.
    field("dist_m", float, required=False, minimum=0, default=0),
])

REP_SCHEMA = Schema("répétition", [
    field("label", str, required=False),
    field("time_s", float, minimum=1),
    # Not `minimum=0`: a zero distance is a division by zero two lines later, and
    # "allure infinie" is not a card, it is a traceback.
    field("dist_m", float, minimum=1),
    field("hr", float, required=False, minimum=1),
    field("recovery", dict, required=False, schema=RECOVERY_SCHEMA,
          note="la récup qui SUIT cette rep, lue sur son tour Strava"),
])

WORKOUT_SCHEMA = Schema("séance de qualité", [
    field("title", str, required=False),
    field("date", str, required=False),
    field("target_pace", object, required=False,
          note='"M:SS" en min/km, ou un nombre de secondes par km'),
    field("note", str, required=False,
          note="ce que l'allure cible ne dit pas (zone, récup) : elle s'affiche déjà"),
    # Two reps minimum: the card reads a DRIFT between the first and the last, and
    # a single rep has no drift to read.
    field("reps", list, minimum=2, item=field("rep", dict, schema=REP_SCHEMA),
          note="dans l'ordre couru"),
])
