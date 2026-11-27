"""Gut training: the carbohydrate target of a fuelled session against its lineage.

The instance declares one `fuelling` lineage at most (`[progression]` of its rulebook):
the range and step of `carbs_g_h`, and `gut_max`, the gut symptoms a fuelled session may
leave for the next target to rise. Each fuelled session run is an exposure: the target it
was prescribed, the carbohydrate taken, the gut's answer. The family
(`progression.default.toml`) turns them into a ceiling, and a revision may prescribe no
target above it: the gut is not trained by override.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from redline.model import rulebook
from redline.model.progression import Dose, Exposure, default
from redline.model.vocabulary import SessionStatus

from . import progression, progression_readings


def exposures(store: Any) -> list[Exposure]:
    """Every fuelled session run, as an exposure of the gut-training lineage."""
    return [progression_readings.fuelling_exposure(
                row["on"], row["target_g_h"], row["carbs_taken_g_h"], row["gut_symptoms"],
                fact=f"session {row['session']}")
            for row in store.fuelled_sessions()]


def ceiling_errors(store: Any, book: rulebook.Rulebook, week: str, revision: dict,
                   today: dt.date) -> list[str]:
    """Each carbohydrate target of `revision` above the ceiling of the gut-training
    lineage; nothing when the instance declares no such lineage."""
    lineage = book.progression.fuelling()
    targets = [session for session in revision.get("sessions") or []
               if session.get("carbs_target_g_h") is not None
               and session.get("status") != SessionStatus.CANCELLED]
    if lineage is None or not targets:
        return []
    family = default()[lineage.family]
    proposal = progression.propose(family, lineage, exposures(store), current=None,
                                   today=today)
    where = f"{week} revision {revision.get('revision')}"
    return [f"{where}.{session['id']}.carbs_target_g_h: {session['carbs_target_g_h']} g/h is "
            f"above the ceiling of {lineage.slug} ({proposal.ceiling}, {proposal.clause}): "
            "one step at most after a fuelled session the gut tolerated"
            for session in targets
            if not progression.allows(family, proposal,
                                      Dose.of(carbs_g_h=float(session["carbs_target_g_h"])))]
