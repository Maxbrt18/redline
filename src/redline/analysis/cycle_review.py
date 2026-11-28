"""The state a preparation's hypotheses are checked against, read once from the store.

`observed` gathers what `redline.model.hypotheses.check` compares with the version's
frozen inputs: the volume of each closed week, the weeks planned low and the assimilation
weeks left out as the generator's start leaves them, the dose each run lineage allows
next, the minutes the habits leave for the week after today, the calibration in force and
the version's race as it stands. `lines` renders the review
for the coach: the Sunday dossier and the plan review read it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from typing import Any

from redline.coach.fr import cycle as text
from redline.model import calibration as calibration_model
from redline.model import cycle_templates, iso_week, paths, rulebook
from redline.model import hypotheses as hypotheses_model
from redline.model import race as race_model
from redline.model.cycle_templates import CycleTemplates
from redline.model.cycles import CycleVersion
from redline.model.hypotheses import Fallen, Observed, Review
from redline.model.vocabulary import CycleKind

from . import cycle_inputs


def observed(store: Any, version: CycleVersion, today: dt.date,
             planned_low: Collection[str]) -> Observed:
    volumes = cycle_inputs.closed_volumes(store, today, planned_low,
                                          len(version.trame.weeks))
    found = calibration_model.in_force(store.calibration_rows())
    race = next((race for race in race_model.typed(store)
                 if race.key == version.race_key and race.goal is not None), None)
    upcoming = iso_week.monday(iso_week.shift(iso_week.of(today), 1))
    return Observed(today, volumes, cycle_inputs.lineages(store, rulebook.current(), today),
                    cycle_inputs.minutes(store, upcoming),
                    found.adopted_on if found is not None else None,
                    cycle_inputs.goal(race) if race is not None else None)


def review(store: Any, templates: CycleTemplates, version: CycleVersion, today: dt.date,
           planned_low: Collection[str]) -> Review:
    """The review of the version in force: a preparation's hypotheses checked, any other
    cycle holding as it is."""
    if version.kind != CycleKind.PREPARATION:
        return hypotheses_model.HOLDS
    return hypotheses_model.check(version.trame,
                                  observed(store, version, today, planned_low),
                                  templates)


def _fallen(found: Fallen) -> str:
    if found.field:
        return text.FALLEN_GOAL[found.field]
    return text.FALLEN_TEXT[found.kind].format(
        value=text.number(found.value) if found.value is not None else text.NO_LIMIT,
        reference=text.number(found.reference) if found.reference is not None
        else text.NO_LIMIT,
        workout=found.workout)


def rendered(found: Review) -> str:
    if not found.fallen:
        return text.HYPOTHESES_HOLD
    return text.HYPOTHESES_FALLEN.format(outcome=text.OUTCOMES[found.outcome],
                                         fallen="; ".join(_fallen(item)
                                                          for item in found.fallen))


def lines(store: Any, today: dt.date) -> list[str]:
    """The review of the version in force, as the coach reads it; nothing for a cycle
    that is not a preparation."""
    version = store.active_cycle()
    if version is None or version.kind != CycleKind.PREPARATION:
        return []
    templates = cycle_templates.load(paths.vault_root())
    return [rendered(review(store, templates, version, today, store.planned_low_weeks()))]
