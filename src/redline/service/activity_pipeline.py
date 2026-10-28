"""Deterministic pipeline for a Strava activity, ahead of any coaching judgement."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from redline.analysis import descent
from redline.integrations import strava
from redline.model import activity as activity_model
from redline.model.activity import Activity, Lap
from redline.storage import StorageError
from redline.storage.repository import OperationalStore

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
    """Capture, enrich and render the state ready for the debrief.

    External calls stay outside any transaction. Each result is then written
    idempotently with its state, so a weather outage never cancels the activity.
    """

    store: OperationalStore
    activity_fetch: ActivityFetch = _activity
    stream_fetch: StreamFetch = _altitude
    weather_fetch: WeatherFetch = _wbgt
    gear_fetch: GearFetch = _gear

    def run(self, activity_id: int, *, fresh_words: bool = False) -> dict[str, Any]:
        """`fresh_words` re-reads title and description for the debrief going out.

        Capture happens as soon as the run arrives, often before the athlete renames it:
        without a re-read the debrief would read the default title. A failed re-read
        keeps the captured words and never blocks the debrief.
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
                # The run's debrief stays the priority. The next webhook or
                # gear-sync replays this idempotent refresh.
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
        from redline.model import debrief

        if debrief.is_quality(session):
            # Read by the service: a quality debrief calls for a judgement.
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
        """RPE, pain and enjoyment already given (notebook): the debrief often goes out later."""
        if not observation_id:
            return {}
        try:
            return self.store.observation_feelings(observation_id)
        except Exception as exc:
            log.warning("Ressentis de l'observation %s illisibles : %s", observation_id, exc)
            return {}

    def _gaps(self, activity_id: int,
              observation_id: Any) -> tuple[list[str], dict | None, Any]:
        """Planned vs done and HR at comparable pace, computed before the turn.

        Also returns the planned session and the run date, which the repetitions card
        reuses. A failed read deprives the debrief of its gaps, never of its departure.
        """
        if not observation_id:
            return [], None, None
        try:
            from redline.analysis import ledger, signals
            from redline.model import debrief, weekly_plan

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
        """The repetitions of a quality session, ready for the card.

        Left to the model's choice, a short debrief could skip the card, so the service
        builds it itself from the Strava laps, like the strength card.
        """
        from redline.model import debrief

        date = f"{day:%d/%m}" if hasattr(day, "strftime") else ""
        card = debrief.quality_card(laps, session, date)
        if card is None:
            # A quality session with no readable laps (no lap, continuous pace) has no
            # card: this shows in the log rather than as a silence.
            log.warning("Séance de qualité %s sans répétitions lisibles dans ses tours : "
                        "pas de carte", activity_id)
        return card

    def _captured_intake(self, activity_id: int) -> dict | None:
        """Re-read the durable fact before any new Strava call.

        A restart between capture and the agent turn must never turn an already
        acquired activity into a new network dependency. Failed enrichments stay
        replayable separately; the core of the debrief is acquired.
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
        """Bounded packet given to the coach, with no polyline nor second provider call."""
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
