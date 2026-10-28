"""Pipeline déterministe d'une activité Strava avant tout jugement de coaching."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from scripts import descent, strava
from scripts.model import activity as activity_model
from scripts.model.activity import Activity, Lap
from scripts.storage import StorageError
from scripts.storage.repository import OperationalStore

from .session_detail import weather_at

ActivityFetch = Callable[[int], dict]
StreamFetch = Callable[[int], list]
WeatherFetch = Callable[[dict], float | None]
GearFetch = Callable[[str], dict]

log = logging.getLogger("redline")


def _activity(activity_id: int) -> dict:
    return strava.call(f"/activities/{activity_id}")


def _altitude(activity_id: int) -> list:
    raw = strava.call(
        f"/activities/{activity_id}/streams",
        {"keys": "altitude", "key_by_type": "true"})
    stream = raw.get("altitude") or {}
    values = stream.get("data") if isinstance(stream, dict) else None
    if not isinstance(values, list):
        raise ValueError("flux d'altitude absent")
    return values


def _wbgt(query: dict) -> float | None:
    return weather_at(query).get("wbgt")


def _gear(gear_id: str) -> dict:
    return strava.distill_gear(strava.call(f"/gear/{gear_id}"))


@dataclass
class ActivityPipeline:
    """Capture, enrichit et rend l'état prêt pour le débrief.

    Les appels externes restent hors transaction. Chaque résultat est ensuite écrit
    idempotemment avec son état ; une panne météo n'annule donc jamais l'activité.
    """

    store: OperationalStore
    activity_fetch: ActivityFetch = _activity
    stream_fetch: StreamFetch = _altitude
    weather_fetch: WeatherFetch = _wbgt
    gear_fetch: GearFetch = _gear

    def run(self, activity_id: int, *, fresh_words: bool = False) -> dict[str, Any]:
        """`fresh_words` relit titre et description pour le débrief qui part.

        La capture se fait dès l'arrivée de la sortie, souvent avant que l'athlète ne la
        renomme : sans relecture, le débrief lirait « Morning Run ». Une relecture
        ratée garde les mots capturés, jamais ne bloque le débrief.
        """
        activity_id = int(activity_id)
        intake = self._captured_intake(activity_id)
        replayed = intake is not None
        if intake is not None:
            activity = Activity.from_json(intake["distilled"])
            if fresh_words:
                activity = self._reread_words(activity)
            captured = {
                "activity_id": activity_id,
                "status": intake["status"],
                "candidate_session_keys": intake.get("candidate_session_keys") or [],
                "observation_id": intake.get("observation_id"),
            }
        else:
            activity = strava.parse_activity(self.activity_fetch(activity_id))
            captured = self.store.ingest_strava_activity(activity)
        gear_state = "not_applicable"
        gear_id = (activity.gear_id or "").strip()
        if gear_id and not replayed:
            try:
                self.store.sync_strava_gear(self.gear_fetch(gear_id))
                gear_state = "known"
            except Exception as exc:
                # Le débrief de la sortie reste prioritaire. Le prochain webhook ou
                # gear-sync rejouera ce rafraîchissement idempotent.
                gear_state = "failed"
                log.warning("Synchronisation du matériel Strava %s échouée : %s",
                            gear_id, exc)
        intake = self.store.strava_intake(activity_id)

        if intake["elevation_loss_state"] in {"pending", "failed"}:
            self._elevation_loss(activity)
        if intake["wbgt_state"] in {"pending", "failed"}:
            self._weather(activity)

        enrichments = self.store.strava_enrichment_state(activity_id)
        snapshot = self._snapshot(activity, captured, enrichments)
        feelings = self._feelings(captured.get("observation_id"))
        if feelings:
            snapshot["feelings"] = feelings
        gaps, session, day = self._gaps(activity_id, captured.get("observation_id"))
        if gaps:
            snapshot["gaps"] = gaps
        from scripts.model import debrief

        if debrief.is_quality(session):
            # Lu par le service : un débrief de qualité engage un jugement (N2).
            snapshot["quality"] = True
            card = self._quality_card(activity_id, activity.laps, session, day)
            if card:
                snapshot["quality_card"] = card
        return {
            **captured,
            "enrichments": enrichments,
            "gear_sync": gear_state,
            "snapshot": snapshot,
        }

    def _reread_words(self, activity: Activity) -> Activity:
        try:
            fresh = strava.parse_activity(self.activity_fetch(activity.id))
        except Exception as exc:
            log.warning("Titre Strava de %s non relu, mots capturés gardés : %s",
                        activity.id, exc)
            return activity
        return replace(activity, name=fresh.name, description=fresh.description)

    def _feelings(self, observation_id: Any) -> dict:
        """RPE, douleur et plaisir déjà donnés (carnet) : le débrief part souvent après."""
        if not observation_id:
            return {}
        try:
            return self.store.observation_feelings(observation_id)
        except Exception as exc:
            log.warning("Ressentis de l'observation %s illisibles : %s", observation_id, exc)
            return {}

    def _gaps(self, activity_id: int,
              observation_id: Any) -> tuple[list[str], dict | None, Any]:
        """Prévu/fait et FC à allure comparable, calculés avant le tour (audit du 11/10).

        Rend aussi la séance prévue et la date de la sortie, que la carte des
        répétitions reprend. Une lecture ratée prive le débrief de ses écarts, jamais
        de son départ.
        """
        if not observation_id:
            return [], None, None
        try:
            from scripts import ledger, signals
            from scripts.model import debrief, weekly_plan

            rows = ledger.parse_ledger(self.store)
            row = next((row for row in rows
                        if row.idempotency_key == f"strava:{activity_id}"), None)
            if row is None:
                return [], None, None
            session = None
            session_id = next((key for key, link in self.store.week_session_links(
                row.week).items() if str(link.get("observation_id")) == str(observation_id)),
                None)
            plan = self.store.load_plan(row.week) if session_id else None
            if plan:
                session = next((item for item in weekly_plan.active(plan)["sessions"]
                                if item["id"] == session_id), None)
            drift = next((item for item in signals.hr_at_pace(rows)
                          if item.date == row.date and item.hr == row.hr_avg), None)
            return debrief.gaps(row, session, drift), session, row.date
        except Exception as exc:
            log.warning("Écarts de l'activité %s non calculés : %s", activity_id, exc)
            return [], None, None

    @staticmethod
    def _quality_card(activity_id: int, laps: tuple[Lap, ...], session: dict | None,
                      day: Any) -> dict | None:
        """Les répétitions d'une séance de qualité, prêtes pour la carte.

        Le 15/10, le premier fractionné avec Redline est parti sans carte : elle était
        laissée au choix du modèle, et un débrief court l'a sautée. Le service la
        construit donc lui-même depuis les tours Strava, comme la carte de renfo.
        """
        from scripts.model import debrief

        date = f"{day:%d/%m}" if hasattr(day, "strftime") else ""
        card = debrief.quality_card(laps, session, date)
        if card is None:
            # Une qualité sans tours lisibles (pas de lap, allure continue) n'a pas de
            # carte : ça se voit au journal plutôt que par un silence.
            log.warning("Séance de qualité %s sans répétitions lisibles dans ses tours : "
                        "pas de carte", activity_id)
        return card

    def _captured_intake(self, activity_id: int) -> dict | None:
        """Relit le fait durable avant tout nouvel appel Strava.

        Un redémarrage entre la capture et le tour agent ne doit jamais transformer
        une activité déjà acquise en nouvelle dépendance réseau. Les enrichissements
        en échec restent rejouables séparément ; le coeur du débrief, lui, est acquis.
        """
        try:
            intake = self.store.strava_intake(activity_id)
        except StorageError:
            return None
        distilled = intake.get("distilled") if isinstance(intake, dict) else None
        if (not isinstance(distilled, dict) or not distilled
                or intake.get("status") == "failed"):
            return None
        return intake

    @staticmethod
    def _snapshot(activity: Activity, captured: dict, enrichments: dict) -> dict:
        """Paquet borné donné au coach, sans polyline ni second appel fournisseur."""
        return {
            "source": "strava_capture",
            "activity": activity_model.summary(activity),
            "laps": activity_model.lap_summaries(activity),
            "matching": {
                "status": captured.get("status"),
                "candidate_session_keys": captured.get("candidate_session_keys") or [],
                "observation_id": captured.get("observation_id"),
            },
            "enrichments": enrichments,
        }

    def _elevation_loss(self, activity: Activity) -> None:
        activity_id, gain = activity.id, activity.elevation_gain_m
        if gain is None:
            self.store.finish_strava_enrichment(
                activity_id, "elevation_loss", unavailable=True)
            return
        try:
            value = descent.descent(float(gain), self.stream_fetch(activity_id))
        except Exception as exc:
            self.store.finish_strava_enrichment(
                activity_id, "elevation_loss", error=str(exc))
            return
        self.store.finish_strava_enrichment(
            activity_id, "elevation_loss", value=value)

    def _weather(self, activity: Activity) -> None:
        activity_id, query = activity.id, activity.weather_query
        if query is None:
            self.store.finish_strava_enrichment(activity_id, "wbgt", unavailable=True)
            return
        try:
            value = self.weather_fetch(query)
        except Exception as exc:
            self.store.finish_strava_enrichment(
                activity_id, "wbgt", error=str(exc))
            return
        self.store.finish_strava_enrichment(
            activity_id, "wbgt", value=value, unavailable=value is None)
