"""The trames the coach writes: a preparation proposed to the athlete, and a new level of
the general preparation.

A proposal is a candidate of the active goal's preparation, generated from the real state
(`redline.analysis.cycle_inputs`), and starts the week after today: before its window
opens there is nothing to propose, and the weeks between are the general preparation's. A
lower peak may be chosen, never a higher one. Proposed while the goal's preparation is in
force, it replans it, for the hypotheses that fell (`fallen`). A new level writes the next
weeks of the general preparation in force at that level.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Any

from redline.model import cycle_templates, iso_week, paths
from redline.model import plan_generator as generator
from redline.model import race as race_model
from redline.model.errors import ConfigError, InputError
from redline.model.plan_generator import Trame
from redline.model.vocabulary import CycleKind, GeneralLevel, HypothesisKind

from . import cycle_inputs, cycle_review


def proposal(store: Any, today: dt.date, candidate: str,
             peak_km: float | None = None) -> tuple[Trame, race_model.Race]:
    """The candidate's trame for the active goal, and the goal."""
    race = race_model.active_goal(store)
    if race is None or race.goal is None or race.on < today:
        raise InputError("no active goal ahead with its distance and terrain: "
                         "`redline fact-write deadline --kind race --priority a`")
    templates = cycle_templates.load(paths.vault_root())
    template = templates.preparation_for(race.goal.distance_km, race.goal.terrain)
    if template is None:
        raise ConfigError(f"no cycle template serves {race.goal.distance_km:g} km on "
                          f"{race.goal.terrain}")
    options = generator.candidate_levers(template)
    if candidate not in options:
        raise InputError(f"no candidate {candidate} (candidates: {', '.join(options)})")
    inputs = cycle_inputs.inputs(store, templates, today)
    goal = cycle_inputs.goal(race)
    trame = generator.preparation(template, goal, options[candidate], inputs,
                                  candidate=candidate)
    starts = trame.weeks[0].label
    if starts != inputs.origin:
        raise InputError(f"the candidate {candidate} starts in {starts}: propose it then, "
                         f"or a candidate that starts in {inputs.origin}")
    if peak_km is not None:
        proposed = max(week.volume_km for week in trame.weeks)
        if peak_km > proposed:
            raise InputError(f"--peak-km {peak_km:g}: above the {proposed:g} km proposed; a "
                             "lower peak may be chosen, never a higher one")
        trame = generator.preparation(template, goal,
                                      replace(options[candidate], peak_km=peak_km), inputs,
                                      candidate=candidate)
    return trame, race


def fallen(store: Any, today: dt.date, race: race_model.Race) -> tuple[HypothesisKind, ...]:
    """The hypotheses of the race's preparation in force that call for its replanning;
    none without one."""
    active = store.active_cycle()
    if active is None or active.kind != CycleKind.PREPARATION or active.race_key != race.key:
        return ()
    return cycle_review.review(store, cycle_templates.load(paths.vault_root()), active, today,
                               store.planned_low_weeks()).replanning


def level(store: Any, today: dt.date, wanted: GeneralLevel) -> Trame:
    """The general preparation at `wanted`, from the week after today."""
    active = store.active_cycle()
    if active is None or active.kind != CycleKind.GENERAL:
        raise InputError("no general preparation in force: a level is the general "
                         "preparation's")
    if active.level == wanted:
        raise InputError(f"the general preparation is already at {wanted}")
    templates = cycle_templates.load(paths.vault_root())
    general = templates.general_at(wanted)
    if general is None:
        raise ConfigError(f"no general template at the level {wanted}")
    trame = generator.general(general, cycle_inputs.inputs(store, templates, today))
    assert trame.weeks[0].label == iso_week.shift(iso_week.of(today), 1)
    return trame
