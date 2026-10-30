"""Input contract and completeness rules for PostgreSQL observations."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from redline.coach.fr import ledger as text

from . import session_kinds
from .errors import ConfigError, InputError
from .schema import Schema, field

UNKNOWN = "?"

SESSION_TYPES = session_kinds.RECORDED
REST_TYPES = session_kinds.READINGS
EFFORT_TYPES = session_kinds.EFFORT
ENJOYMENT_TYPES = session_kinds.ENJOYMENT


@dataclass(frozen=True)
class ObservationInput:
    """An option of `redline ledger-add` and its destination in the typed observation."""

    name: str
    pattern: str
    metric: str = ""
    decimals: int | None = None
    required: bool = False
    unavailable: bool = False
    note: str = ""


STRAVA_ID_PATTERN = r"\d{9,12}"
INPUTS = (
    ObservationInput("date", r"\d{4}-\d{2}-\d{2}", required=True),
    ObservationInput("type", "|".join(SESSION_TYPES), required=True),
    ObservationInput("routine", r"[a-z0-9][a-z0-9-]{0,39}",
                     note="the strength routine, as its note declares it"),
    ObservationInput("km", r"\d+(\.\d{1,2})?", metric="distance_km", decimals=2),
    ObservationInput("time", r"\d{1,2}:\d{2}(:\d{2})?", metric="duration_s"),
    ObservationInput("pace", r"\d:\d{2}", metric="pace_s_per_km", note="min/km"),
    ObservationInput("hr_avg", r"\d{2,3}", metric="hr_avg_bpm", decimals=0),
    ObservationInput("hr_max", r"\d{2,3}", metric="hr_max_bpm", decimals=0),
    ObservationInput("cadence", r"\d{3}", metric="cadence_spm", decimals=0,
                     note="steps/min, both feet -- Strava shows one leg"),
    ObservationInput("dplus", r"\d+", metric="elevation_gain_m", decimals=0),
    ObservationInput("dminus", r"\d+", metric="elevation_loss_m", decimals=0),
    ObservationInput("wbgt", r"-?\d{1,2}", metric="wbgt_c", decimals=0,
                     note="°C, thermal load -- never a cap"),
    ObservationInput("rpe", r"(10|\d)(\.\d)?", metric="rpe", decimals=1,
                     unavailable=True),
    ObservationInput("enjoyment", r"10|\d", metric="enjoyment", decimals=0,
                     unavailable=True),
    ObservationInput("strava", STRAVA_ID_PATTERN),
    ObservationInput("note", r".*"),
)


def input_field(name: str) -> ObservationInput:
    for item in INPUTS:
        if item.name == name:
            return item
    raise KeyError(f"unknown observation field: {name}")


def _accepted_pattern(item: ObservationInput) -> str:
    return f"({item.pattern}|\\?)" if item.unavailable else item.pattern


INPUT_SCHEMA = Schema("observation", [
    field(item.name, str, required=item.required, pattern=_accepted_pattern(item),
          note=item.note)
    for item in INPUTS
])


class LedgerError(InputError):
    """An observation rejected at the input boundary."""


def canonical_value(name: str, value: str) -> str:
    """Round a raw value only if the field expects such a precision."""
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
    """The non-pain ratings an observation needs to be usable. Pain is requested per
    zone (`requires_pain`)."""
    needed: list[str] = []
    if kind in EFFORT_TYPES:
        needed.append("rpe")
    if kind in ENJOYMENT_TYPES:
        needed.append("enjoyment")
    return tuple(needed)


# ------------------------------------------------------------- pain per zone
#: The pain column of a zone, as a gap names it: « pain:knee ».
PAIN_COLUMN = "pain"
#: The columns the athlete declares, in the order they are asked; the others are measured.
DECLARED_COLUMNS = (PAIN_COLUMN, "rpe", "enjoyment")
PAIN_PATTERN = r"(10|\d)(\.\d)?"
PAIN_NOTE = "the peak of the session, not the average"


def requires_pain(kind: str) -> bool:
    """Pain for every watched zone is due for effort and rest observations."""
    return kind in REST_TYPES or kind in EFFORT_TYPES


def pain_column(zone: str) -> str:
    return f"{PAIN_COLUMN}:{zone}"


def column_base(column: str) -> str:
    """`pain:knee` -> `pain`; other columns stay as they are."""
    return column.split(":", 1)[0]


def column_zone(column: str) -> str | None:
    base, _, zone = column.partition(":")
    return zone or None if base == PAIN_COLUMN else None


def parse_pains(values: list[str], zones: list[str]) -> dict[str, str]:
    """`--pain knee=2 --pain ankle=?` -> {zone: raw value}.

    A value without a zone is allowed only when a single zone is watched: guessing which
    of several would file the pain of one zone under another. An unknown zone, a zone
    named twice, or an unreadable value is rejected.
    """
    readings: dict[str, str] = {}
    for raw in values:
        zone, sep, value = raw.partition("=")
        if not sep:
            if len(zones) != 1:
                raise LedgerError(
                    "--pain: name the zone, e.g. --pain knee=2 (zones: "
                    + (", ".join(zones) or "no monitored zone") + ")")
            zone, value = zones[0], raw
        zone, value = zone.strip(), value.strip()
        if zone not in zones:
            raise LedgerError(f"--pain: unknown zone {zone!r} (zones: "
                              + (", ".join(zones) or "no monitored zone") + ")")
        if zone in readings:
            raise LedgerError(f"--pain: zone {zone!r} given twice")
        readings[zone] = value
    return readings


def pain_facts(pains: dict[str, dict]) -> list[str]:
    """`douleur knee=2` for each known zone of an observation."""
    return [text.PAIN_READING.format(zone=zone, value=reading["value"]) for zone, reading in sorted(pains.items())
            if reading.get("state") == "known"]


def pending_pains(pains: dict[str, dict]) -> list[str]:
    """The zones still expected for an observation, named as ledger-complete names them."""
    return [f"pain:{zone}" for zone, reading in sorted(pains.items())
            if reading.get("state") == "pending"]


def strength_pain_facts(pains: dict[str, dict]) -> list[str]:
    """`knee 1->2/10`: the pain of a strength session, before then after."""
    def shown(value: object) -> str:
        return "?" if value is None else str(value)
    return [f"{zone} {shown(moments.get('before'))}->{shown(moments.get('after'))}/10"
            for zone, moments in sorted(pains.items())]


def pain_reading(value: str) -> dict:
    """A raw pain value -> the typed reading (`?` = asked, then lost)."""
    if value == UNKNOWN:
        return {"state": "unavailable"}
    text = value.replace(",", ".")
    try:
        number = Decimal(text).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise LedgerError(f"unreadable pain: {value!r} (0 to 10, by half points)") from None
    if not re.fullmatch(PAIN_PATTERN, str(number)):
        raise LedgerError(f"pain off the scale: {value!r} (0 to 10)")
    return {"state": "known", "value": float(number)}


def completeness_from() -> dt.date:
    """The first day whose observations must be complete (`[ledger] complete_from` in
    `redline.toml`): an instance that adopts the contract late is not asked for its
    history. Without a readable instance, every day."""
    from . import instance, paths
    try:
        return instance.load(paths.vault_root()).complete_from
    except ConfigError:
        return dt.date.min


COMPLETION_WINDOW = dt.timedelta(days=2)

METRIC_COLUMNS = {
    "rpe": "rpe", "enjoyment": "enjoyment",
    "elevation_gain_m": "dplus", "elevation_loss_m": "dminus",
    "carbs_g_h": "carbs_g_h", "gut_symptoms": "gut_symptoms",
}

#: Awaited only after a session that tested a fuelling target: linking it marks them
#: pending (`session_cycles`), and only that marking makes them due.
FUELLING_METRICS = ("carbs_g_h", "gut_symptoms")


def missing_observation_fields(observation: dict) -> list[str]:
    """Fields still required for a typed observation; pain is checked zone by zone."""
    kind = observation.get("kind", "")
    metrics = observation.get("measurements", {})
    required: list[str] = []
    if kind not in REST_TYPES:
        if kind in EFFORT_TYPES:
            required.append("rpe")
        if kind in ENJOYMENT_TYPES:
            required.append("enjoyment")
    missing = [
        METRIC_COLUMNS[metric] for metric in required
        if (metrics.get(metric) or {}).get("state") not in {"known", "unavailable"}
    ]
    if requires_pain(kind):
        missing += [pain_column(zone) for zone, reading in sorted(
            (observation.get("pains") or {}).items())
            if reading.get("state") not in {"known", "unavailable"}]
    missing += [METRIC_COLUMNS[metric] for metric in FUELLING_METRICS
                if (metrics.get(metric) or {}).get("state") == "pending"]
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
    start = completeness_from()
    for observation in observations:
        day = observation.get("occurred_on")
        if not isinstance(day, dt.date) or day < start:
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
    return [text.MISSING_CELLS.format(date=gap["date"], type=gap["type"], join=", ".join(gap["missing"]))
            for gap in lapsed_observation_fields(observations, today)]


def cell_key(entry: dict, column: str) -> str:
    """Stable identity of a rating to request."""
    return f"{entry['date']}:{entry['type']}:{column}"
