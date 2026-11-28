"""A version of a training cycle: a trame, as written once and kept.

A cycle (`cycle_key`) lives through versions; a version holds its trame
(`redline.model.plan_generator.Trame`) whole, with the inputs it was generated from, and
never changes but by its state. At most one version is active at a time; the planner keeps
one active (`redline.model.cycle_transitions`).

This module owns the translation between a trame and its three tables (`cycle_versions`,
`cycle_weeks`, `cycle_key_sessions`): the rows are written from a trame and read back into
the same trame, so the storage adds nothing a trame does not say.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, fields
from typing import Any

from . import iso_week
from .cycle_templates import Intensity, Range
from .plan_generator import Goal, Inputs, Key, Levers, Risk, Segment, Trame, TrameRisk, Week
from .progression import Dose
from .vocabulary import (
    CycleCause,
    CycleKind,
    CycleVersionState,
    GeneralLevel,
    IntensityRef,
    KeyRole,
    PlanPhase,
    RaceTerrain,
)


@dataclass(frozen=True)
class CycleVersion:
    id: uuid.UUID
    cycle_key: uuid.UUID
    version: int
    state: CycleVersionState
    trame: Trame
    cause: CycleCause
    #: The race the version prepares, or the race a recovery follows; its deadline key.
    race_key: str | None = None
    level: GeneralLevel | None = None
    #: The day the version was written, in UTC.
    created_on: dt.date | None = None
    #: The day a proposal expires, in UTC; None for a version never proposed.
    expires_on: dt.date | None = None

    @property
    def kind(self) -> CycleKind:
        return self.trame.kind

    @property
    def last_week(self) -> str:
        return self.trame.weeks[-1].label

    def week(self, label: str) -> Week | None:
        return self.trame.week(label)

    def keys_of(self, label: str) -> list[Key]:
        return [key for key in self.trame.keys if key.week == label]


def starts_on(trame: Trame) -> dt.date:
    return iso_week.monday(trame.weeks[0].label)


def ends_on(trame: Trame) -> dt.date:
    return iso_week.monday(trame.weeks[-1].label) + dt.timedelta(days=6)


# ------------------------------------------------------------------ to rows


def _dose(dose: Dose | None) -> list[list[Any]] | None:
    return None if dose is None else [[name, value] for name, value in dose.values]


def hypotheses_of(inputs: Inputs) -> dict:
    """The inputs as JSON: every field of `Inputs`, the day and the doses spelled out."""
    data = {spec.name: getattr(inputs, spec.name) for spec in fields(Inputs)}
    data["calibration_on"] = (inputs.calibration_on.isoformat()
                              if inputs.calibration_on is not None else None)
    data["lineages"] = [[workout, _dose(dose)] for workout, dose in inputs.lineages]
    return data


def version_values(trame: Trame) -> dict:
    """The columns of `cycle_versions` the trame fills."""
    levers = trame.levers
    return {
        "kind": str(trame.kind), "template": trame.template, "candidate": trame.candidate,
        "starts_on": starts_on(trame), "ends_on": ends_on(trame),
        "levers": None if levers is None else {
            "specific_weeks": levers.specific_weeks, "deload_every": levers.deload_every,
            "second_quality": str(levers.second_quality), "peak_km": levers.peak_km},
        "goal": None if trame.goal is None else {
            "on": trame.goal.on.isoformat(), "distance_km": trame.goal.distance_km,
            "terrain": trame.goal.terrain, "goal_time_s": trame.goal.goal_time_s},
        "hypotheses": hypotheses_of(trame.hypotheses),
        "risks": [{"kind": str(risk.kind), "value": risk.value, "reference": risk.reference,
                   "workout": risk.workout, "phase": str(risk.phase) if risk.phase else None}
                  for risk in trame.risks],
    }


def week_values(trame: Trame) -> list[dict]:
    """The rows of `cycle_weeks`, one a week."""
    rows = []
    for week in trame.weeks:
        segment = week.segment
        rows.append({
            "iso_week": week.label, "phase": str(week.phase), "deload": week.deload,
            "volume_km": week.volume_km, "long_run_km": week.long_run_km,
            "qualities": week.qualities,
            "segment_km": segment.km if segment else None,
            "segment_ref": str(segment.intensity.ref) if segment else None,
            "segment_low": segment.intensity.band.low if segment else None,
            "segment_high": segment.intensity.band.high if segment else None})
    return rows


def key_values(trame: Trame) -> list[dict]:
    """The rows of `cycle_key_sessions`, in the trame's order."""
    return [{"iso_week": key.week, "position": key.position, "workout": key.workout,
             "role": str(key.role), "dose": _dose(key.dose),
             "intensity_ref": str(key.intensity.ref) if key.intensity else None,
             "intensity_low": key.intensity.band.low if key.intensity else None,
             "intensity_high": key.intensity.band.high if key.intensity else None}
            for key in trame.keys]


# ------------------------------------------------------------------ from rows


def _float(value: Any) -> float:
    return float(value)


def _read_dose(value: Any) -> Dose | None:
    return None if value is None else Dose(tuple((str(name), float(amount))
                                                 for name, amount in value))


def _intensity(ref: Any, low: Any, high: Any) -> Intensity | None:
    if ref is None:
        return None
    return Intensity(IntensityRef(ref), Range(_float(low), _float(high)))


def inputs_of(data: dict) -> Inputs:
    """The inputs a version's `hypotheses` hold."""
    values: dict[str, Any] = {spec.name: data[spec.name] for spec in fields(Inputs)
                              if spec.name in data}
    values["calibration_on"] = (dt.date.fromisoformat(data["calibration_on"])
                                if data.get("calibration_on") else None)
    lineages = []
    for workout, dose in data.get("lineages") or []:
        read = _read_dose(dose)
        if read is not None:
            lineages.append((str(workout), read))
    values["lineages"] = tuple(lineages)
    return Inputs(**values)


def _week(row: dict) -> Week:
    intensity = _intensity(row.get("segment_ref"), row.get("segment_low"),
                           row.get("segment_high"))
    segment = (Segment(_float(row["segment_km"]), intensity)
               if intensity is not None and row.get("segment_km") is not None else None)
    return Week(row["iso_week"], PlanPhase(row["phase"]), bool(row["deload"]),
                _float(row["volume_km"]), _float(row["long_run_km"]), int(row["qualities"]),
                segment)


def _key(row: dict) -> Key:
    return Key(row["iso_week"], int(row["position"]), row["workout"], KeyRole(row["role"]),
               _read_dose(row.get("dose")),
               _intensity(row.get("intensity_ref"), row.get("intensity_low"),
                          row.get("intensity_high")))


def trame_of(version: dict, weeks: list[dict], keys: list[dict]) -> Trame:
    """The trame a version's rows hold."""
    levers = version.get("levers")
    goal = version.get("goal")
    risks = tuple(Risk(TrameRisk(risk["kind"]), float(risk["value"]), float(risk["reference"]),
                       risk.get("workout") or "",
                       PlanPhase(risk["phase"]) if risk.get("phase") else None)
                  for risk in version.get("risks") or [])
    ordered_keys = sorted(keys, key=lambda row: (row["iso_week"], row["position"]))
    return Trame(
        CycleKind(version["kind"]), version["template"], version.get("candidate") or "",
        tuple(_week(row) for row in sorted(weeks, key=lambda row: row["iso_week"])),
        tuple(_key(row) for row in ordered_keys), risks, inputs_of(version["hypotheses"]),
        None if not levers else Levers(
            int(levers["specific_weeks"]), int(levers["deload_every"]),
            PlanPhase(levers["second_quality"]),
            float(levers["peak_km"]) if levers.get("peak_km") is not None else None),
        None if not goal else Goal(
            dt.date.fromisoformat(goal["on"]), float(goal["distance_km"]),
            RaceTerrain(goal["terrain"]) if goal.get("terrain") else None,
            int(goal["goal_time_s"]) if goal.get("goal_time_s") is not None else None))


def announcement(version: CycleVersion) -> dict:
    """What the message of a version put in force says: its kind, level, first week, length
    and highest weekly volume."""
    weeks = version.trame.weeks
    return {"kind": str(version.kind), "level": version.level, "starts_on": weeks[0].label,
            "weeks": len(weeks), "volume_km": max(week.volume_km for week in weeks)}


def proposal(version: CycleVersion, race: str, expires_on: dt.date) -> dict:
    """What the message of a proposal says, with what the athlete's answer names."""
    weeks = version.trame.weeks
    return {"version_id": str(version.id), "race": race, "candidate": version.trame.candidate,
            "starts_on": weeks[0].label, "weeks": len(weeks),
            "peak_km": max(week.volume_km for week in weeks),
            "long_run_km": max(week.long_run_km for week in weeks),
            "flagships": sum(1 for key in version.trame.keys if key.role == KeyRole.FLAGSHIP),
            "risks": len(version.trame.risks), "expires_on": expires_on.isoformat()}


def payload_of(trame: Trame) -> dict:
    """The trame as JSON, the rows it is written to: what a command carries."""
    version = version_values(trame)
    version["starts_on"] = version["starts_on"].isoformat()
    version["ends_on"] = version["ends_on"].isoformat()
    return {"version": version, "weeks": week_values(trame), "keys": key_values(trame)}


def from_payload(payload: dict) -> Trame:
    return trame_of(payload["version"], payload["weeks"], payload["keys"])


def from_rows(version: dict, weeks: list[dict], keys: list[dict]) -> CycleVersion:
    """A version as stored, with its trame."""
    created, expires = version.get("created_at"), version.get("expires_at")
    return CycleVersion(
        version["id"], version["cycle_key"], int(version["version"]),
        CycleVersionState(version["state"]), trame_of(version, weeks, keys),
        CycleCause(version["cause"]["kind"]), version.get("race_key"),
        GeneralLevel(version["level"]) if version.get("level") else None,
        created.astimezone(dt.UTC).date() if isinstance(created, dt.datetime) else None,
        expires.astimezone(dt.UTC).date() if isinstance(expires, dt.datetime) else None)
