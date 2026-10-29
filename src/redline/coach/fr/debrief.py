"""What a debrief compares, as the coach reads it."""

from __future__ import annotations


def number(value: float) -> str:
    """`6,5`: at most two decimals, with a decimal comma."""
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def signed(value: float) -> str:
    """`+1,5`, `-2`."""
    return ("+" if value > 0 else "") + number(value)

DISTANCE_GAP = "distance {done} km pour un {label} de {planned} ({signed})"

DURATION_GAP = "durée {minutes:.0f} min pour un plafond de {duration_cap_min} ({signed})"

HR_GAP = "FC moyenne {hr} pour un plafond de {hr_cap_bpm} ({signed})"

AT_MOST = "au plus {high}"

RPE_GAP = "RPE {rpe} pour une cible {span}"

HR_AT_PACE = "FC à allure comparable : "

SESSION = "Séance"

TARGET = "cible"

CAP = "plafond"
