"""Observations, measurements, per-zone pain, exposures and daily state."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from redline.model import ledger

from ..database import StorageError
from ..schema import (
    MEASUREMENT_DEFINITIONS,
    daily_checkins,
    observation_metrics,
    observations,
    pain_readings,
    pain_zones,
    session_exposures,
    strava_activity_intakes,
    weekly_instruments,
)
from ._common import (
    HR_DOUBTS,
    RESOLVING_KINDS,
    StoreBase,
    _audit,
    _jsonable,
    _plain,
    _request_projection,
    _uuid,
)


class Observations(StoreBase):
    def add_observation(self, payload: dict, *, actor: str = "application") -> uuid.UUID:
        with self.database.transaction() as connection:
            return self.add_observation_in(connection, payload, actor=actor)

    def add_observation_in(self, connection: Connection, payload: dict,
                           *, actor: str = "application") -> uuid.UUID:
        observation_id = _uuid(payload.get("id"))
        strava_id = payload.get("strava_activity_id")
        values = {
            "id": observation_id,
            "athlete_id": payload.get("athlete_id", self.athlete_id),
            "occurred_on": payload["occurred_on"],
            "started_at": payload.get("started_at"),
            "kind": payload["kind"],
            "routine": payload.get("routine"),
            "idempotency_key": payload["idempotency_key"],
            "strava_activity_id": int(strava_id) if strava_id else None,
            "gear_id": payload.get("gear_id"),
            "note": payload.get("note", ""),
            "source": payload.get("source", actor),
            "supersedes_id": payload.get("supersedes_id"),
        }
        statement: Any = pg_insert(observations).values(**values).on_conflict_do_nothing(
            constraint="uq_observations_idempotency").returning(observations.c.id)
        inserted = connection.execute(statement).scalar_one_or_none()
        if inserted is None:
            existing = connection.execute(sa.select(observations).where(
                observations.c.athlete_id == values["athlete_id"],
                observations.c.idempotency_key == values["idempotency_key"],
            )).mappings().one()
            stored_metrics = connection.execute(sa.select(observation_metrics).where(
                observation_metrics.c.observation_id == existing["id"]
            )).mappings().one()
            expected = self._metric_values(payload.get("measurements", {}))
            comparable = ("occurred_on", "started_at", "kind", "routine", "strava_activity_id",
                          "gear_id", "note", "supersedes_id")
            stored_pains = self._pains_of_in(connection, existing["id"])
            given_pains = {zone: reading for zone, reading in self._pain_values_in(
                connection, values["kind"], payload.get("pains", {})).items()
                if zone in payload.get("pains", {})}
            if any(existing[key] != values[key] for key in comparable) or any(
                    _plain(stored_metrics[key]) != _plain(expected[key]) for key in expected
            ) or any(stored_pains.get(zone) != {"state": reading["state"],
                                                 "value": _plain(reading["value"])}
                     for zone, reading in given_pains.items()):
                raise StorageError(
                    f"collision d'idempotence pour l'observation {values['idempotency_key']!r}")
            return existing["id"]
        observation_id = inserted

        connection.execute(observation_metrics.insert().values(
            observation_id=observation_id,
            **self._metric_values(payload.get("measurements", {})),
        ))
        pains = self._pain_values_in(connection, values["kind"], payload.get("pains", {}))
        for zone, reading in pains.items():
            connection.execute(pain_readings.insert().values(
                observation_id=observation_id, zone=zone, athlete_id=values["athlete_id"],
                **reading))
        event = {key: _plain(value) for key, value in values.items()}
        _audit(connection, actor, "observation.created", "observation", observation_id, event)
        if values["kind"] in RESOLVING_KINDS:
            for zone, reading in pains.items():
                if reading["state"] in ("known", "unavailable"):
                    self.settle_observed_predictions_in(
                        connection, values["kind"], values["occurred_on"], zone,
                        float(reading["value"]) if reading["state"] == "known" else None,
                        actor=actor)
        if values["strava_activity_id"] and values["started_at"]:
            self._auto_match_observation_in(
                connection, observation_id, payload.get("session_key"), actor=actor)
            connection.execute(strava_activity_intakes.update().where(
                strava_activity_intakes.c.activity_id == values["strava_activity_id"]
            ).values(status="matched", observation_id=observation_id, error="",
                     updated_at=sa.func.now()))
        _request_projection(connection)
        return observation_id

    @staticmethod
    def _metric_values(measurements: dict) -> dict:
        unknown = sorted(set(measurements) - set(MEASUREMENT_DEFINITIONS))
        if unknown:
            raise StorageError("mesure inconnue : " + ", ".join(unknown))
        values = {}
        for metric in MEASUREMENT_DEFINITIONS:
            measurement = measurements.get(metric, {"state": "not_applicable"})
            state = measurement.get("state", "known")
            values[f"{metric}_state"] = state
            values[metric] = measurement.get("value") if state == "known" else None
        return values

    def sync_pain_zones(self, zones: list[tuple[str, str]], *,
                        actor: str = "application") -> None:
        """Align the reference on `[[injuries]]`: a removed zone becomes inactive.

        Never deleted: its past readings stay attached and readable.
        """
        with self.database.transaction() as connection:
            declared = {slug for slug, _ in zones}
            for slug, label in zones:
                connection.execute(pg_insert(pain_zones).values(
                    athlete_id=self.athlete_id, slug=slug, label=label, active=True,
                ).on_conflict_do_update(
                    index_elements=[pain_zones.c.athlete_id, pain_zones.c.slug],
                    set_={"label": label, "active": True}))
            connection.execute(pain_zones.update().where(
                pain_zones.c.athlete_id == self.athlete_id,
                pain_zones.c.slug.not_in(declared) if declared else sa.true(),
            ).values(active=False))
            _audit(connection, actor, "pain_zones.synced", "athlete",
                   uuid.uuid5(uuid.NAMESPACE_URL, f"athlete:{self.athlete_id}"),
                   {"zones": sorted(declared)})

    def pain_zones(self) -> list[dict]:
        """Monitored zones, active first, in declaration order."""
        with self.database.engine.connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(
                pain_zones.c.slug, pain_zones.c.label, pain_zones.c.active,
            ).where(pain_zones.c.athlete_id == self.athlete_id).order_by(
                pain_zones.c.active.desc(), pain_zones.c.created_at,
                pain_zones.c.slug)).mappings()]

    def _active_zones_in(self, connection: Connection) -> list[str]:
        return list(connection.execute(sa.select(pain_zones.c.slug).where(
            pain_zones.c.athlete_id == self.athlete_id, pain_zones.c.active,
        ).order_by(pain_zones.c.created_at, pain_zones.c.slug)).scalars())

    def _pain_values_in(self, connection: Connection, kind: str,
                        given: dict) -> dict[str, dict[str, Any]]:
        """One reading per active zone: the given value, else `pending` if the observation
        type requires pain, `not_applicable` otherwise. An unknown or removed zone is
        rejected rather than filed under another one."""
        active = self._active_zones_in(connection)
        unknown = sorted(set(given) - set(active))
        if unknown:
            raise StorageError("zone de douleur inconnue ou inactive : " + ", ".join(unknown)
                               + (f" (actives : {', '.join(active)})" if active
                                  else " (aucune zone surveillée)"))
        default = "pending" if ledger.requires_pain(kind) else "not_applicable"
        readings = {}
        for zone in active:
            reading = given.get(zone, {"state": default})
            state = reading.get("state", "known")
            readings[zone] = {"state": state,
                              "value": reading.get("value") if state == "known" else None}
        return readings

    @staticmethod
    def _pains_of_in(connection: Connection, observation_id: uuid.UUID) -> dict[str, dict]:
        return {row["zone"]: {"state": row["state"], "value": _plain(row["value"])}
                for row in connection.execute(sa.select(
                    pain_readings.c.zone, pain_readings.c.state, pain_readings.c.value,
                ).where(pain_readings.c.observation_id == observation_id)).mappings()}

    def complete_measurement(self, observation_id: uuid.UUID, metric: str,
                             value: float | None, *, zone: str | None = None,
                             unavailable: bool = False,
                             actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.complete_measurement_in(connection, observation_id, metric, value,
                                         zone=zone, unavailable=unavailable, actor=actor)

    def doubt_hr(self, observation_id: uuid.UUID, reason: str, note: str,
                 *, actor: str = "application") -> None:
        """Flag a known average HR as doubtful, once, with its reason.

        The measurement stays immutable: only the annotation says what it is worth. The
        free-text reason (the athlete's words) goes to the audit log.
        """
        with self.database.transaction() as connection:
            self.doubt_hr_in(connection, observation_id, reason, note, actor=actor)

    def doubt_hr_in(self, connection: Connection, observation_id: uuid.UUID, reason: str,
                    note: str, *, actor: str = "application") -> None:
        if reason not in HR_DOUBTS:
            raise StorageError(f"motif de FC douteuse inconnu : {reason}")
        if not note.strip():
            raise StorageError("une FC douteuse porte la cause déclarée")
        result = connection.execute(observation_metrics.update().where(
            observation_metrics.c.observation_id == observation_id,
            observation_metrics.c.hr_avg_bpm_state == "known",
            observation_metrics.c.hr_doubt.is_(None),
        ).values(hr_doubt=reason))
        if result.rowcount != 1:
            raise StorageError("FC absente, inconnue ou déjà déclarée douteuse")
        _audit(connection, actor, "measurement.hr_doubted", "observation",
               observation_id, {"reason": reason, "note": note.strip()})
        _request_projection(connection)

    def complete_measurement_in(self, connection: Connection, observation_id: uuid.UUID,
                                metric: str, value: float | None, *, zone: str | None = None,
                                unavailable: bool = False,
                                actor: str = "application") -> None:
        if metric == "pain":
            self._complete_pain_in(connection, observation_id, zone, value,
                                   unavailable=unavailable, actor=actor)
            return
        if zone is not None:
            raise StorageError(f"seule la douleur se mesure par zone, pas {metric}")
        if metric not in MEASUREMENT_DEFINITIONS:
            raise StorageError(f"mesure inconnue : {metric}")
        state = "unavailable" if unavailable else "known"
        state_column = observation_metrics.c[f"{metric}_state"]
        result = connection.execute(observation_metrics.update().where(
            observation_metrics.c.observation_id == observation_id,
            # Only `known` is immutable. `not_applicable` is not a measurement: it is the
            # DEFAULT of any column absent at creation (`_metric_values`). Locking it
            # would make a session recorded without its figures permanently
            # incompletable. An absence default is not data: it can be filled in.
            state_column.in_(("pending", "unavailable", "not_applicable")),
        ).values({state_column: state,
                  observation_metrics.c[metric]: None if unavailable else value}))
        if result.rowcount != 1:
            raise StorageError("mesure absente, déjà connue ou ambiguë")
        _audit(connection, actor, "measurement.completed", "observation", observation_id,
               {"metric": metric, "state": state, "value": value})
        _request_projection(connection)

    def _complete_pain_in(self, connection: Connection, observation_id: uuid.UUID,
                          zone: str | None, value: float | None, *, unavailable: bool,
                          actor: str) -> None:
        """Like any measurement: only a `known` reading is immutable."""
        zones = list(connection.execute(sa.select(pain_readings.c.zone).where(
            pain_readings.c.observation_id == observation_id)).scalars())
        if zone is None:
            if len(zones) != 1:
                raise StorageError("douleur : nomme la zone (" + (", ".join(sorted(zones))
                                   or "aucune zone sur cette observation") + ")")
            zone = zones[0]
        state = "unavailable" if unavailable else "known"
        result = connection.execute(pain_readings.update().where(
            pain_readings.c.observation_id == observation_id,
            pain_readings.c.zone == zone,
            pain_readings.c.state.in_(("pending", "unavailable", "not_applicable")),
        ).values(state=state, value=None if unavailable else value,
                 recorded_at=sa.func.now()))
        if result.rowcount != 1:
            raise StorageError(f"douleur {zone} absente, déjà connue ou ambiguë")
        _audit(connection, actor, "measurement.completed", "observation", observation_id,
               {"metric": "pain", "zone": zone, "state": state, "value": value})
        kind, day = connection.execute(sa.select(
            observations.c.kind, observations.c.occurred_on).where(
                observations.c.id == observation_id)).one()
        if kind in RESOLVING_KINDS:
            self.settle_observed_predictions_in(
                connection, kind, day, zone,
                None if unavailable or value is None else float(value), actor=actor)
        _request_projection(connection)

    def link_activity(self, idempotency_key: str, strava_activity_id: int, *,
                      actor: str = "application") -> uuid.UUID:
        """Attach an existing observation to its exact Strava identity.

        An observation created in the chat can precede the activity's arrival at Strava.
        The fact is not duplicated: its external identity is added to the existing row.
        Replaying the same link is idempotent; targeting another activity is an explicit
        conflict.
        """
        with self.database.transaction() as connection:
            return self.link_activity_in(
                connection, idempotency_key, strava_activity_id, actor=actor)

    def link_activity_in(self, connection: Connection, idempotency_key: str,
                         strava_activity_id: int, *, actor: str) -> uuid.UUID:
        row = connection.execute(sa.select(
            observations.c.id, observations.c.strava_activity_id,
        ).where(
            observations.c.athlete_id == self.athlete_id,
            observations.c.idempotency_key == idempotency_key,
        ).with_for_update()).one_or_none()
        if row is None:
            raise StorageError(f"observation inconnue : {idempotency_key}")
        observation_id, current = row
        wanted = int(strava_activity_id)
        if current == wanted:
            return observation_id
        if current is not None:
            raise StorageError(
                f"observation {idempotency_key} déjà rattachée à l'activité {current}")
        connection.execute(observations.update().where(
            observations.c.id == observation_id,
        ).values(strava_activity_id=wanted))
        _audit(connection, actor, "activity.linked", "observation", observation_id,
               {"idempotency_key": idempotency_key, "strava_activity_id": wanted})
        _request_projection(connection)
        return observation_id
    def observation_id(self, idempotency_key: str) -> uuid.UUID | None:
        with self.database.engine.connect() as connection:
            return connection.execute(sa.select(observations.c.id).where(
                observations.c.athlete_id == self.athlete_id,
                observations.c.idempotency_key == idempotency_key,
            )).scalar_one_or_none()

    def observation_rows(self) -> list[dict]:
        query = sa.select(observations, observation_metrics).join(
            observation_metrics,
            observation_metrics.c.observation_id == observations.c.id,
        ).where(
            observations.c.athlete_id == self.athlete_id,
        ).order_by(observations.c.occurred_on.desc(), observations.c.created_at.desc())
        with self.database.engine.connect() as connection:
            result = []
            for row in connection.execute(query).mappings():
                entry = {
                    "id": row["id"],
                    "occurred_on": row["occurred_on"],
                    "started_at": row["started_at"],
                    "kind": row["kind"],
                    "routine": row["routine"],
                    "idempotency_key": row["idempotency_key"],
                    "strava_activity_id": row["strava_activity_id"],
                    "gear_id": row["gear_id"],
                    "note": row["note"],
                    "source": row["source"],
                    "hr_doubt": row["hr_doubt"],
                    "measurements": {},
                }
                for metric in MEASUREMENT_DEFINITIONS:
                    entry["measurements"][metric] = {
                        "state": row[f"{metric}_state"], "value": _plain(row[metric])}
                entry["pains"] = {}
                result.append(entry)
            by_id = {entry["id"]: entry for entry in result}
            for reading in connection.execute(sa.select(
                    pain_readings.c.observation_id, pain_readings.c.zone,
                    pain_readings.c.state, pain_readings.c.value,
            ).where(pain_readings.c.athlete_id == self.athlete_id).order_by(
                    pain_readings.c.zone)).mappings():
                owner = by_id.get(reading["observation_id"])
                if owner is not None:
                    owner["pains"][reading["zone"]] = {
                        "state": reading["state"], "value": _plain(reading["value"])}
            return result
    def upsert_exposure(self, payload: dict, *, actor: str = "application") -> uuid.UUID:
        with self.database.transaction() as connection:
            return self.upsert_exposure_in(connection, payload, actor=actor)

    def upsert_exposure_in(self, connection: Connection, payload: dict,
                           *, actor: str = "application") -> uuid.UUID:
        key = str(payload["exposure_key"]).strip()
        observation_id = connection.execute(sa.select(observations.c.id).where(
            observations.c.athlete_id == self.athlete_id,
            observations.c.idempotency_key == payload["observation_key"],
        )).scalar_one_or_none()
        if observation_id is None:
            raise StorageError(f"observation inconnue : {payload['observation_key']}")
        existing = connection.execute(sa.select(session_exposures).where(
            session_exposures.c.athlete_id == self.athlete_id,
            session_exposures.c.exposure_key == key,
        ).with_for_update()).mappings().one_or_none()
        values = {
            "observation_id": observation_id,
            "exposure_type": payload["exposure_type"],
            "origin": payload["origin"],
            "status": payload["status"],
            "duration_min": payload.get("duration_min"),
            "target_value": payload.get("target_value"),
            "target_unit": payload.get("target_unit"),
            "tolerance": payload.get("tolerance", "unknown"),
            "note": payload.get("note", ""),
        }
        if existing is not None:
            comparable = {name: _plain(existing[name]) for name in values}
            incoming = {name: _plain(value) for name, value in values.items()}
            if comparable == incoming:
                return existing["id"]
            # The intent can be refined or corrected later, like its dose or tolerance.
            # Same key, same exposure; the audit log keeps the trace.
            exposure_id = existing["id"]
            connection.execute(session_exposures.update().where(
                session_exposures.c.id == exposure_id).values(
                    **values, updated_at=sa.func.now()))
        else:
            exposure_id = _uuid(payload.get("id"))
            connection.execute(session_exposures.insert().values(
                id=exposure_id, athlete_id=self.athlete_id, exposure_key=key, **values))
        _audit(connection, actor, "exposure.upsert", "session_exposure", exposure_id,
               {"exposure_key": key, **_jsonable(values)})
        _request_projection(connection)
        return exposure_id

    def exposure_rows(self, *, confirmed_only: bool = False) -> list[dict]:
        query = sa.select(
            session_exposures,
            observations.c.idempotency_key.label("observation_key"),
            observations.c.occurred_on,
        ).join(observations, observations.c.id == session_exposures.c.observation_id).where(
            session_exposures.c.athlete_id == self.athlete_id)
        if confirmed_only:
            query = query.where(session_exposures.c.status == "confirmed")
        query = query.order_by(observations.c.occurred_on.desc(),
                               session_exposures.c.created_at.desc())
        with self.database.engine.connect() as connection:
            return [{name: _plain(value) for name, value in dict(row).items()}
                    for row in connection.execute(query).mappings()]
    def put_daily(self, row: dict, *, actor: str = "application") -> None:
        with self.database.transaction() as connection:
            self.put_daily_in(connection, row, actor=actor)

    def put_daily_in(self, connection: Connection, row: dict,
                     *, actor: str = "application") -> None:
        values = {
            "athlete_id": row.get("athlete_id", self.athlete_id),
            "checked_on": row.get("checked_on", row.get("day")), "sleep": row.get("sleep"),
            "life_load": row.get("life_load"), "desire": row.get("desire"),
            "note": row.get("note", ""),
        }
        statement = pg_insert(daily_checkins).values(**values).on_conflict_do_update(
            index_elements=[daily_checkins.c.athlete_id, daily_checkins.c.checked_on],
            set_={key: value for key, value in values.items()
                  if key not in ("athlete_id", "checked_on")},
        )
        aggregate_id = uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"daily:{values['athlete_id']}:{values['checked_on']}")
        connection.execute(statement)
        _audit(connection, actor, "daily.upserted", "daily", aggregate_id,
               {key: _plain(value) for key, value in values.items()})
        _request_projection(connection)

    def daily_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(daily_checkins).where(
                daily_checkins.c.athlete_id == self.athlete_id,
            ).order_by(daily_checkins.c.checked_on)).mappings()
            return [dict(row) for row in rows]

    def weekly_instrument_rows(self) -> list[dict]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(sa.select(weekly_instruments).where(
                weekly_instruments.c.athlete_id == self.athlete_id,
            ).order_by(weekly_instruments.c.iso_week)).mappings()
            return [dict(row) for row in rows]
