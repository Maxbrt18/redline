"""Contrat d'entrée et règles de complétude des observations PostgreSQL."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .schema import Schema, field

UNKNOWN = "?"

SESSION_TYPES = (
    "EF", "SL", "seuil", "côtes", "lignes", "cadence", "course", "rando",
    "vélo", "renfoA", "renfoB", "instruments", "lecture", "reveil", "palpation",
)
REST_TYPES = ("lecture", "reveil")
EFFORT_TYPES = (
    "EF", "SL", "seuil", "côtes", "lignes", "cadence", "course", "rando",
    "vélo", "renfoA", "renfoB",
)
ENJOYMENT_TYPES = (
    "EF", "SL", "seuil", "côtes", "lignes", "cadence", "course", "rando",
)


@dataclass(frozen=True)
class ObservationInput:
    """Une option de `ledger-add.py` et sa destination dans l'observation typée."""

    name: str
    pattern: str
    option: str = ""
    metric: str = ""
    decimals: int | None = None
    required: bool = False
    unavailable: bool = False
    note: str = ""


STRAVA_ID_PATTERN = r"\d{9,12}"
INPUTS = (
    ObservationInput("Date", r"\d{4}-\d{2}-\d{2}", required=True),
    ObservationInput("Type", "|".join(SESSION_TYPES), required=True),
    ObservationInput("km", r"\d+(\.\d{1,2})?", metric="distance_km", decimals=2),
    ObservationInput("Temps", r"\d{1,2}:\d{2}(:\d{2})?", metric="duration_s"),
    ObservationInput("Allure", r"\d:\d{2}", metric="pace_s_per_km", note="min/km"),
    ObservationInput("FCmoy", r"\d{2,3}", metric="hr_avg_bpm", decimals=0),
    ObservationInput("FCmax", r"\d{2,3}", metric="hr_max_bpm", decimals=0),
    ObservationInput("Cad", r"\d{3}", metric="cadence_spm", decimals=0,
                     note="pas/min, deux pieds — Strava affiche une jambe"),
    ObservationInput("D+", r"\d+", option="dplus", metric="elevation_gain_m", decimals=0),
    ObservationInput("D-", r"\d+", option="dmoins", metric="elevation_loss_m", decimals=0),
    ObservationInput("WBGT", r"-?\d{1,2}", metric="wbgt_c", decimals=0,
                     note="°C, charge thermique — jamais un plafond"),
    ObservationInput("RPE", r"(10|\d)(\.\d)?", metric="rpe", decimals=1,
                     unavailable=True),
    ObservationInput("Dlr", r"(10|\d)(\.\d)?", metric="pain", decimals=1,
                     unavailable=True, note="le pic de la séance, pas la moyenne"),
    ObservationInput("Plaisir", r"10|\d", metric="enjoyment", decimals=0,
                     unavailable=True),
    ObservationInput("Strava", STRAVA_ID_PATTERN),
    ObservationInput("Note", r".*"),
)


def input_field(name: str) -> ObservationInput:
    for item in INPUTS:
        if item.name == name:
            return item
    raise KeyError(f"champ d'observation inconnu : {name}")


def _accepted_pattern(item: ObservationInput) -> str:
    return f"({item.pattern}|\\?)" if item.unavailable else item.pattern


INPUT_SCHEMA = Schema("observation", [
    field(item.name, str, required=item.required, pattern=_accepted_pattern(item),
          note=item.note)
    for item in INPUTS
])


class LedgerError(ValueError):
    """Une observation refusée à la frontière d'entrée."""


def canonical_value(name: str, value: str) -> str:
    """Arrondit une valeur brute seulement si le champ attend une telle précision."""
    text = value.strip()
    if text in ("", UNKNOWN):
        return text
    try:
        item = input_field(name)
    except KeyError:
        return text
    if re.fullmatch(_accepted_pattern(item), text) or item.decimals is None:
        return text
    try:
        number = Decimal(text.replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return text
    quantum = Decimal(1).scaleb(-item.decimals)
    rounded = str(number.quantize(quantum, rounding=ROUND_HALF_UP))
    return rounded if re.fullmatch(_accepted_pattern(item), rounded) else text


def required_columns(kind: str) -> tuple[str, ...]:
    """Les ressentis nécessaires pour qu'une observation ait une valeur clinique."""
    if kind in REST_TYPES:
        return ("Dlr",)
    needed: list[str] = []
    if kind in EFFORT_TYPES:
        needed += ["RPE", "Dlr"]
    if kind in ENJOYMENT_TYPES:
        needed.append("Plaisir")
    return tuple(needed)


# Les observations antérieures ont été écrites sous un contrat moins exigeant.
COMPLETENESS_FROM = dt.date(2026, 7, 30)
COMPLETION_WINDOW = dt.timedelta(days=2)

METRIC_COLUMNS = {
    "rpe": "RPE", "pain": "Dlr", "enjoyment": "Plaisir",
    "elevation_gain_m": "D+", "elevation_loss_m": "D-",
}


def missing_observation_fields(observation: dict) -> list[str]:
    """Champs encore requis d'une observation typée."""
    kind = observation.get("kind", "")
    metrics = observation.get("measurements", {})
    required: list[str] = []
    if kind in REST_TYPES:
        required.append("pain")
    else:
        if kind in EFFORT_TYPES:
            required += ["rpe", "pain"]
        if kind in ENJOYMENT_TYPES:
            required.append("enjoyment")
    missing = [
        METRIC_COLUMNS[metric] for metric in required
        if (metrics.get(metric) or {}).get("state") not in {"known", "unavailable"}
    ]
    if observation.get("strava_activity_id") is not None:
        missing += [
            METRIC_COLUMNS[metric]
            for metric in ("elevation_gain_m", "elevation_loss_m")
            if (metrics.get(metric) or {}).get("state") != "known"
        ]
    return missing


def _observation_gaps(observations: list[dict], today: dt.date,
                      *, pending: bool) -> list[dict]:
    gaps = []
    for observation in observations:
        day = observation.get("occurred_on")
        if not isinstance(day, dt.date) or day < COMPLETENESS_FROM:
            continue
        if (today - day < COMPLETION_WINDOW) != pending:
            continue
        missing = missing_observation_fields(observation)
        if missing:
            gaps.append({"date": day.isoformat(), "type": observation.get("kind", "?"),
                         "missing": missing})
    return sorted(gaps, key=lambda entry: entry["date"])


def pending_observation_fields(observations: list[dict], today: dt.date) -> list[dict]:
    return _observation_gaps(observations, today, pending=True)


def lapsed_observation_fields(observations: list[dict], today: dt.date) -> list[dict]:
    return _observation_gaps(observations, today, pending=False)


def observation_completeness_errors(observations: list[dict], today: dt.date) -> list[str]:
    return [f"{gap['date']} {gap['type']} : {', '.join(gap['missing'])} non renseigné(s)"
            for gap in lapsed_observation_fields(observations, today)]


def cell_key(entry: dict, column: str) -> str:
    """Identité stable d'un ressenti à réclamer."""
    return f"{entry['date']}:{entry['type']}:{column}"
