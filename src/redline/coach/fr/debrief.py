"""What a debrief compares, as the coach reads it."""

from __future__ import annotations


def number(value: float) -> str:
    """`6,5`: at most two decimals, with a decimal comma."""
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def signed(value: float) -> str:
    """`+1,5`, `-2`."""
    return ("+" if value > 0 else "") + number(value)


def pace(seconds_per_km: float) -> str:
    """`5:42`: minutes and seconds per kilometre, as a runner reads a pace."""
    total = round(seconds_per_km)
    return f"{total // 60}:{total % 60:02d}"


def kilometres(numbers: tuple[int, ...]) -> str:
    """`km 3, 7 et 8`: the kilometres named by a gap, the first eight at most."""
    if len(numbers) > 8:
        return "km " + ", ".join(str(n) for n in numbers[:8]) + "…"
    if len(numbers) == 1:
        return f"km {numbers[0]}"
    return "km " + ", ".join(str(n) for n in numbers[:-1]) + f" et {numbers[-1]}"


DISTANCE_GAP = "distance {done} km pour un {label} de {planned} ({signed})"

DURATION_GAP = "durée {minutes:.0f} min pour un plafond de {duration_cap_min} ({signed})"

HR_GAP = "FC moyenne {hr} pour un plafond de {hr_cap_bpm} ({signed})"

AT_MOST = "au plus {high}"

RPE_GAP = "RPE {rpe} pour une cible {span}"

HR_AT_PACE = "FC à allure comparable : "

SESSION = "Séance"

TARGET = "cible"

CAP = "plafond"

SPLIT_HR_ABOVE_CAP = ("FC au-dessus du plafond de {cap} sur {count} km ({kms}), "
                      "jusqu'à {peak}")

SPLIT_FAST_START = "départ rapide : 1er km en {first} pour {median} sur les autres ({signed} %)"

SPLIT_FADE = "fin qui faiblit : {kms} en {last} contre {before} avant ({signed} %)"

SPLIT_DECOUPLING = "découplage allure/FC de {value} % entre les deux moitiés"
