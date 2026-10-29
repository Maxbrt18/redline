"""Short labels the service puts into the coach's notes, by key."""

from __future__ import annotations

import datetime as dt

#: The reasons a prediction failed or could not be tested, as the coach reads them.
PREDICTION_REASONS = {
    "optimistic": "réfutée optimiste", "cautious": "réfutée prudente",
    "instrument": "void instrument", "exposure": "void exposition",
    "scope": "void portée", "superseded": "void remplacée",
}

#: How a missing ledger cell is asked for: the unit asked, not the unit stored.
CELL_QUESTIONS = {
    "rpe": "l'effort ressenti sur la séance ENTIÈRE (RPE, un entier)",
    "pain": "la douleur au pic (/10, zone par zone)",
    "enjoyment": "le plaisir pris (/10)",
    "dplus": "le D+",
    "dminus": "le D-",
}

NO_CAPTION = "(aucune légende)"


def delay(minutes: int) -> str:
    """`42 min`, `1 h 05`."""
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"


def received(moment: dt.datetime) -> str:
    return f"{moment:%d/%m à %Hh%M}"


def clock_time(moment: dt.datetime) -> str:
    return f"{moment:%Hh%M}"
